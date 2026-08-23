#include "SimCameraStreamerActor.h"

#include "SimUdpControlledActor.h"

#include "Common/TcpSocketBuilder.h"
#include "Components/SceneCaptureComponent2D.h"
#include "Components/SceneComponent.h"
#include "Engine/TextureRenderTarget2D.h"
#include "HAL/PlatformTime.h"
#include "ImageUtils.h"
#include "IPAddress.h"
#include "Interfaces/IPv4/IPv4Address.h"
#include "Interfaces/IPv4/IPv4Endpoint.h"
#include "SocketSubsystem.h"
#include "Sockets.h"

DEFINE_LOG_CATEGORY_STATIC(LogSimCameraStreamer, Log, All);

namespace SimCameraProtocol
{
constexpr int32 HeaderSize = 64;
constexpr uint8 Version = 1;
constexpr uint8 EncodingJpeg = 1;
constexpr uint32 PoseMetadataValidFlag = 0x00000001;
constexpr uint8 Magic[4] = {'S', 'I', 'M', 'G'};

void WriteU16BE(uint8* Data, uint16 Value)
{
    Data[0] = static_cast<uint8>(Value >> 8);
    Data[1] = static_cast<uint8>(Value);
}

void WriteU32BE(uint8* Data, uint32 Value)
{
    Data[0] = static_cast<uint8>(Value >> 24);
    Data[1] = static_cast<uint8>(Value >> 16);
    Data[2] = static_cast<uint8>(Value >> 8);
    Data[3] = static_cast<uint8>(Value);
}

void WriteU64BE(uint8* Data, uint64 Value)
{
    WriteU32BE(Data, static_cast<uint32>(Value >> 32));
    WriteU32BE(Data + 4, static_cast<uint32>(Value));
}
} // namespace SimCameraProtocol

ASimCameraStreamerActor::ASimCameraStreamerActor()
{
    USceneComponent* Root = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
    SetRootComponent(Root);

    SceneCapture =
        CreateDefaultSubobject<USceneCaptureComponent2D>(TEXT("SceneCapture"));
    SceneCapture->SetupAttachment(Root);
    SceneCapture->bCaptureEveryFrame = false;
    SceneCapture->bCaptureOnMovement = false;
    SceneCapture->bAlwaysPersistRenderingState = true;
    SceneCapture->CaptureSource = ESceneCaptureSource::SCS_FinalColorLDR;

    PrimaryActorTick.bCanEverTick = true;
    PrimaryActorTick.TickGroup = TG_PostUpdateWork;
}

void ASimCameraStreamerActor::OnConstruction(const FTransform& Transform)
{
    Super::OnConstruction(Transform);
    UpdateCaptureSettings();
}

void ASimCameraStreamerActor::BeginPlay()
{
    Super::BeginPlay();

    UpdateCaptureSettings();
    StreamStartSeconds = FPlatformTime::Seconds();

    if (IsValid(PoseBridge))
    {
        // The bridge already runs in PrePhysics while this sensor runs in
        // PostUpdateWork. The explicit prerequisite also documents and
        // preserves the intended order if either tick group changes later.
        AddTickPrerequisiteActor(PoseBridge);
    }
    else if (bRequireAppliedPose || bCaptureOnlyOnNewPose)
    {
        UE_LOG(LogSimCameraStreamer, Warning,
            TEXT("PoseBridge is unset, but this camera requires pose metadata"));
    }

    if (IsValid(FollowActor) && FollowActor->GetRootComponent())
    {
        // Keep the viewpoint authored in the level. Attachment computes its
        // relative sensor mount and then makes it follow the controlled actor.
        AttachToComponent(
            FollowActor->GetRootComponent(),
            FAttachmentTransformRules::KeepWorldTransform);
    }
    else
    {
        UE_LOG(LogSimCameraStreamer, Warning,
            TEXT("FollowActor is unset; the SIL camera will remain stationary"));
    }

    if (!InitializeRenderTarget())
    {
        bStreamingEnabled = false;
        return;
    }

    if (bStreamingEnabled && !OpenListenSocket())
    {
        bStreamingEnabled = false;
    }
    bLastStreamingEnabled = bStreamingEnabled;
}

void ASimCameraStreamerActor::EndPlay(
    const EEndPlayReason::Type EndPlayReason)
{
    if (SceneCapture)
    {
        SceneCapture->TextureTarget = nullptr;
    }
    CloseSockets();
    RenderTarget = nullptr;
    Super::EndPlay(EndPlayReason);
}

void ASimCameraStreamerActor::Tick(float DeltaSeconds)
{
    Super::Tick(DeltaSeconds);
    ++StreamerTickId;

    if (bStreamingEnabled != bLastStreamingEnabled)
    {
        if (bStreamingEnabled)
        {
            if (!OpenListenSocket())
            {
                bStreamingEnabled = false;
            }
        }
        else
        {
            CloseSockets();
        }
        bLastStreamingEnabled = bStreamingEnabled;
    }

    if (!bStreamingEnabled)
    {
        return;
    }

    AcceptClient();

    if (ClientSocket &&
        ClientSocket->GetConnectionState() != SCS_Connected)
    {
        CloseClientSocket();
    }

    FlushPendingSend();

    if (!bStreamingEnabled || !ClientSocket || !SceneCapture || !RenderTarget)
    {
        return;
    }

    const double CaptureInterval = 1.0 / FMath::Max(0.1f, CaptureRateHz);
    CaptureAccumulatorSeconds += static_cast<double>(DeltaSeconds);
    if (CaptureAccumulatorSeconds < CaptureInterval)
    {
        return;
    }

    // Discard excess elapsed time rather than producing catch-up bursts.
    CaptureAccumulatorSeconds = FMath::Fmod(
        CaptureAccumulatorSeconds,
        CaptureInterval);

    if (!PendingSendBuffer.IsEmpty())
    {
        ++SkippedFrameCount;
        return;
    }

    if (CaptureAndQueueFrame())
    {
        // Most localhost JPEGs complete in this call. Partial sends remain in
        // PendingSendBuffer and resume on later ticks without blocking.
        FlushPendingSend();
    }
}

void ASimCameraStreamerActor::UpdateCaptureSettings()
{
    if (!SceneCapture)
    {
        return;
    }

    SceneCapture->bCaptureEveryFrame = false;
    SceneCapture->bCaptureOnMovement = false;
    // Explicit captures still need a view state for eye adaptation,
    // pre-exposure, temporal history, and Lumen scene data.
    SceneCapture->bAlwaysPersistRenderingState = true;
    SceneCapture->CaptureSource = ESceneCaptureSource::SCS_FinalColorLDR;
    SceneCapture->FOVAngle = FMath::Clamp(FieldOfViewDegrees, 5.0f, 170.0f);
}

bool ASimCameraStreamerActor::InitializeRenderTarget()
{
    if (ImageWidth < 16 || ImageWidth > 3840 ||
        ImageHeight < 16 || ImageHeight > 2160)
    {
        UE_LOG(LogSimCameraStreamer, Error,
            TEXT("Image dimensions %dx%d are outside the supported POC range"),
            ImageWidth, ImageHeight);
        return false;
    }

    if (JpegQuality < 1 || JpegQuality > 100)
    {
        UE_LOG(LogSimCameraStreamer, Error,
            TEXT("JpegQuality %d must be between 1 and 100"), JpegQuality);
        return false;
    }

    RenderTarget = NewObject<UTextureRenderTarget2D>(
        this,
        TEXT("SimCameraRenderTarget"),
        RF_Transient);
    if (!RenderTarget)
    {
        UE_LOG(LogSimCameraStreamer, Error,
            TEXT("Could not create the SIL camera render target"));
        return false;
    }

    RenderTarget->ClearColor = FLinearColor::Black;
    RenderTarget->bAutoGenerateMips = false;
    RenderTarget->RenderTargetFormat = ETextureRenderTargetFormat::RTF_RGBA8_SRGB;
    RenderTarget->TargetGamma = 2.2f;

    // InitCustomFormat updates bForceLinearGamma as well as the format.
    // Assigning RenderTargetFormat directly does not run the editor-only
    // PostEditChangeProperty path that normally keeps those values in sync.
    RenderTarget->InitCustomFormat(
        ImageWidth,
        ImageHeight,
        PF_B8G8R8A8,
        false);
    RenderTarget->UpdateResourceImmediate(true);
    SceneCapture->TextureTarget = RenderTarget;

    UE_LOG(LogSimCameraStreamer, Display,
        TEXT("Initialized SIL camera at %dx%d, %.1f Hz, JPEG quality %d"),
        ImageWidth, ImageHeight, CaptureRateHz, JpegQuality);
    return true;
}

bool ASimCameraStreamerActor::OpenListenSocket()
{
    if (ListenPort < 1 || ListenPort > 65535)
    {
        UE_LOG(LogSimCameraStreamer, Error,
            TEXT("ListenPort %d is invalid"), ListenPort);
        return false;
    }

    FIPv4Address Address;
    if (!FIPv4Address::Parse(BindAddress, Address))
    {
        UE_LOG(LogSimCameraStreamer, Error,
            TEXT("Invalid camera BindAddress '%s'"), *BindAddress);
        return false;
    }

    const FIPv4Endpoint Endpoint(Address, static_cast<uint16>(ListenPort));
    ListenSocket = FTcpSocketBuilder(TEXT("SimCameraListenSocket"))
        .AsReusable()
        .AsNonBlocking()
        .BoundToEndpoint(Endpoint)
        .Listening(1)
        .WithSendBufferSize(4 * 1024 * 1024);

    if (!ListenSocket)
    {
        UE_LOG(LogSimCameraStreamer, Error,
            TEXT("Could not bind camera TCP listener to %s:%d"),
            *BindAddress, ListenPort);
        return false;
    }

    UE_LOG(LogSimCameraStreamer, Display,
        TEXT("Listening for a SIL camera client on TCP %s:%d"),
        *BindAddress, ListenPort);
    return true;
}

void ASimCameraStreamerActor::AcceptClient()
{
    if (!ListenSocket || ClientSocket)
    {
        return;
    }

    bool bHasPendingConnection = false;
    if (!ListenSocket->HasPendingConnection(bHasPendingConnection) ||
        !bHasPendingConnection)
    {
        return;
    }

    TSharedRef<FInternetAddr> RemoteAddress =
        ISocketSubsystem::Get(PLATFORM_SOCKETSUBSYSTEM)->CreateInternetAddr();
    ClientSocket = ListenSocket->Accept(
        *RemoteAddress,
        TEXT("SimCameraClientSocket"));
    if (!ClientSocket)
    {
        return;
    }

    ClientSocket->SetNonBlocking(true);
    ClientSocket->SetNoDelay(true);
    int32 ActualSendBufferSize = 0;
    ClientSocket->SetSendBufferSize(4 * 1024 * 1024, ActualSendBufferSize);
    bClientConnected = true;
    CaptureAccumulatorSeconds = 1.0 / FMath::Max(0.1f, CaptureRateHz);
    LastSendProgressSeconds = FPlatformTime::Seconds();
    LastCapturedRunId = 0;
    LastCapturedPoseSequence = 0;
    bHasCapturedPose = false;

    UE_LOG(LogSimCameraStreamer, Display,
        TEXT("SIL camera client connected from %s (send buffer %d bytes)"),
        *RemoteAddress->ToString(true), ActualSendBufferSize);
}

void ASimCameraStreamerActor::CloseClientSocket()
{
    if (ClientSocket)
    {
        ClientSocket->Close();
        ISocketSubsystem::Get(PLATFORM_SOCKETSUBSYSTEM)->DestroySocket(
            ClientSocket);
        ClientSocket = nullptr;
        UE_LOG(LogSimCameraStreamer, Display,
            TEXT("SIL camera client disconnected"));
    }

    bClientConnected = false;
    PendingSendBuffer.Reset();
    PendingSendOffset = 0;
}

void ASimCameraStreamerActor::CloseSockets()
{
    CloseClientSocket();

    if (ListenSocket)
    {
        ListenSocket->Close();
        ISocketSubsystem::Get(PLATFORM_SOCKETSUBSYSTEM)->DestroySocket(
            ListenSocket);
        ListenSocket = nullptr;
    }
}

void ASimCameraStreamerActor::FlushPendingSend()
{
    if (!ClientSocket || PendingSendBuffer.IsEmpty())
    {
        return;
    }

    const int32 RemainingBytes =
        PendingSendBuffer.Num() - PendingSendOffset;
    int32 BytesSent = 0;
    const bool bSendSucceeded = ClientSocket->Send(
        PendingSendBuffer.GetData() + PendingSendOffset,
        RemainingBytes,
        BytesSent);

    const double NowSeconds = FPlatformTime::Seconds();
    if (bSendSucceeded && BytesSent > 0)
    {
        PendingSendOffset += BytesSent;
        LastSendProgressSeconds = NowSeconds;

        if (PendingSendOffset == PendingSendBuffer.Num())
        {
            PendingSendBuffer.Reset();
            PendingSendOffset = 0;
            ++SentFrameCount;
        }
        return;
    }

    if (!bSendSucceeded)
    {
        ISocketSubsystem* SocketSubsystem =
            ISocketSubsystem::Get(PLATFORM_SOCKETSUBSYSTEM);
        const ESocketErrors Error = SocketSubsystem->GetLastErrorCode();
        if (Error != SE_EWOULDBLOCK && Error != SE_EINPROGRESS)
        {
            UE_LOG(LogSimCameraStreamer, Warning,
                TEXT("Camera TCP send failed: %s"),
                SocketSubsystem->GetSocketError(Error));
            CloseClientSocket();
            return;
        }
    }

    if (NowSeconds - LastSendProgressSeconds >
        FMath::Max(0.25f, ClientStallTimeoutSeconds))
    {
        UE_LOG(LogSimCameraStreamer, Warning,
            TEXT("Camera client made no send progress for %.2f seconds"),
            NowSeconds - LastSendProgressSeconds);
        CloseClientSocket();
    }
}

bool ASimCameraStreamerActor::CaptureAndQueueFrame()
{
    uint64 AppliedRunId = 0;
    uint32 AppliedPoseSequence = 0;
    uint64 AppliedSimulationTimeNs = 0;
    const bool bPoseMetadataValid = IsValid(PoseBridge) &&
        PoseBridge->GetLastAppliedPoseMetadata(
            AppliedRunId,
            AppliedPoseSequence,
            AppliedSimulationTimeNs);

    if (bRequireAppliedPose && !bPoseMetadataValid)
    {
        return false;
    }

    if (bCaptureOnlyOnNewPose)
    {
        if (!bPoseMetadataValid ||
            (bHasCapturedPose && AppliedRunId == LastCapturedRunId &&
             AppliedPoseSequence == LastCapturedPoseSequence))
        {
            return false;
        }
    }

    const double CaptureRequestSeconds = FPlatformTime::Seconds();
    const uint64 CaptureMonotonicNs = static_cast<uint64>(
        (CaptureRequestSeconds - StreamStartSeconds) * 1'000'000'000.0);

    SceneCapture->CaptureScene();

    FImage Image;
    if (!FImageUtils::GetRenderTargetImage(RenderTarget, Image))
    {
        UE_LOG(LogSimCameraStreamer, Warning,
            TEXT("Could not read the SIL camera render target"));
        ++SkippedFrameCount;
        return false;
    }
    const double ReadbackDoneSeconds = FPlatformTime::Seconds();
    LastReadbackMilliseconds = static_cast<float>(
        (ReadbackDoneSeconds - CaptureRequestSeconds) * 1000.0);

    if (Image.Format == ERawImageFormat::BGRA8 && Image.RawData.Num() >= 4)
    {
        const uint8* Pixels = Image.RawData.GetData();
        const int64 PixelCount = Image.RawData.Num() / 4;
        uint8 Minimum = 255;
        uint8 Maximum = 0;
        uint64 Sum = 0;
        for (int64 PixelIndex = 0; PixelIndex < PixelCount; ++PixelIndex)
        {
            const uint8 Blue = Pixels[PixelIndex * 4];
            const uint8 Green = Pixels[PixelIndex * 4 + 1];
            const uint8 Red = Pixels[PixelIndex * 4 + 2];
            Minimum = FMath::Min(
                Minimum,
                FMath::Min(Red, FMath::Min(Green, Blue)));
            Maximum = FMath::Max(
                Maximum,
                FMath::Max(Red, FMath::Max(Green, Blue)));
            Sum += static_cast<uint64>(Red) + Green + Blue;
        }

        LastPixelMinimum = Minimum;
        LastPixelMaximum = Maximum;
        LastPixelMean = static_cast<float>(
            static_cast<double>(Sum) /
            (static_cast<double>(PixelCount) * 3.0));

        if (CapturedFrameCount == 0)
        {
            UE_LOG(LogSimCameraStreamer, Display,
                TEXT("First camera readback RGB min/mean/max: %d / %.1f / %d"),
                LastPixelMinimum, LastPixelMean, LastPixelMaximum);
        }

        if (!bLoggedNonUniformReadback &&
            LastPixelMinimum < 250 &&
            LastPixelMaximum - LastPixelMinimum >= 5)
        {
            bLoggedNonUniformReadback = true;
            UE_LOG(LogSimCameraStreamer, Display,
                TEXT("Camera readback became non-uniform on frame %lld: "
                     "RGB min/mean/max %d / %.1f / %d"),
                static_cast<long long>(NextCameraFrameId),
                LastPixelMinimum, LastPixelMean, LastPixelMaximum);
        }
    }

    TArray64<uint8> CompressedJpeg;
    if (!FImageUtils::CompressImage(
            CompressedJpeg,
            TEXT("jpg"),
            Image,
            JpegQuality))
    {
        UE_LOG(LogSimCameraStreamer, Warning,
            TEXT("Could not JPEG-compress a SIL camera frame"));
        ++SkippedFrameCount;
        return false;
    }
    const double CompressionDoneSeconds = FPlatformTime::Seconds();
    LastJpegMilliseconds = static_cast<float>(
        (CompressionDoneSeconds - ReadbackDoneSeconds) * 1000.0);

    if (CompressedJpeg.IsEmpty() ||
        CompressedJpeg.Num() > static_cast<int64>(MAX_uint32) ||
        CompressedJpeg.Num() >
            static_cast<int64>(MAX_int32 - SimCameraProtocol::HeaderSize))
    {
        UE_LOG(LogSimCameraStreamer, Warning,
            TEXT("Compressed camera frame has unsupported size %lld"),
            static_cast<long long>(CompressedJpeg.Num()));
        ++SkippedFrameCount;
        return false;
    }

    LastJpegBytes = static_cast<int32>(CompressedJpeg.Num());
    PendingSendBuffer.SetNumUninitialized(
        SimCameraProtocol::HeaderSize + LastJpegBytes);

    uint8* Header = PendingSendBuffer.GetData();
    FMemory::Memzero(Header, SimCameraProtocol::HeaderSize);
    FMemory::Memcpy(Header, SimCameraProtocol::Magic, 4);
    Header[4] = SimCameraProtocol::Version;
    Header[5] = SimCameraProtocol::EncodingJpeg;
    SimCameraProtocol::WriteU16BE(
        Header + 6,
        static_cast<uint16>(SimCameraProtocol::HeaderSize));
    SimCameraProtocol::WriteU32BE(
        Header + 8,
        bPoseMetadataValid ? SimCameraProtocol::PoseMetadataValidFlag : 0);
    SimCameraProtocol::WriteU32BE(
        Header + 12,
        static_cast<uint32>(LastJpegBytes));
    SimCameraProtocol::WriteU64BE(Header + 16, AppliedRunId);
    SimCameraProtocol::WriteU32BE(Header + 24, AppliedPoseSequence);
    // The render target is allocated at BeginPlay. Serialize its real size so
    // an incidental runtime edit of the configuration properties cannot make
    // the wire metadata disagree with the JPEG dimensions.
    SimCameraProtocol::WriteU16BE(
        Header + 28,
        static_cast<uint16>(RenderTarget->SizeX));
    SimCameraProtocol::WriteU16BE(
        Header + 30,
        static_cast<uint16>(RenderTarget->SizeY));
    SimCameraProtocol::WriteU64BE(Header + 32, AppliedSimulationTimeNs);
    SimCameraProtocol::WriteU64BE(Header + 40, NextCameraFrameId);
    SimCameraProtocol::WriteU64BE(Header + 48, StreamerTickId);
    SimCameraProtocol::WriteU64BE(Header + 56, CaptureMonotonicNs);

    FMemory::Memcpy(
        PendingSendBuffer.GetData() + SimCameraProtocol::HeaderSize,
        CompressedJpeg.GetData(),
        LastJpegBytes);
    PendingSendOffset = 0;
    LastSendProgressSeconds = FPlatformTime::Seconds();

    ++CapturedFrameCount;
    ++NextCameraFrameId;
    if (bPoseMetadataValid)
    {
        LastCapturedRunId = AppliedRunId;
        LastCapturedPoseSequence = AppliedPoseSequence;
        bHasCapturedPose = true;
    }

    return true;
}
