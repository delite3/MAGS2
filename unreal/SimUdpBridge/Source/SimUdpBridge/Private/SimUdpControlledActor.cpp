#include "SimUdpControlledActor.h"

#include "Common/UdpSocketBuilder.h"
#include "Components/SceneComponent.h"
#include "IPAddress.h"
#include "Interfaces/IPv4/IPv4Address.h"
#include "Interfaces/IPv4/IPv4Endpoint.h"
#include "SocketSubsystem.h"
#include "Sockets.h"

DEFINE_LOG_CATEGORY_STATIC(LogSimUdpBridge, Log, All);

namespace SimUdpProtocol
{
constexpr int32 CommandPacketSize = 56;
constexpr int32 AckPacketSize = 20;
constexpr int32 MaxPacketsPerTick = 1024;
constexpr uint8 Version = 2;
constexpr uint8 StartRunFlag = 0x01;
constexpr uint8 KnownCommandFlags = StartRunFlag;
constexpr uint8 AckApplied = 0;
constexpr uint8 AckInvalidPacket = 1;
constexpr uint8 AckRejected = 2;
constexpr uint8 AckApplyFailed = 3;
constexpr uint8 CommandMagic[4] = {'S', 'U', 'D', 'P'};
constexpr uint8 AckMagic[4] = {'S', 'A', 'C', 'K'};

uint16 ReadU16BE(const uint8* Data)
{
    return (static_cast<uint16>(Data[0]) << 8) | static_cast<uint16>(Data[1]);
}

uint32 ReadU32BE(const uint8* Data)
{
    return (static_cast<uint32>(Data[0]) << 24) |
           (static_cast<uint32>(Data[1]) << 16) |
           (static_cast<uint32>(Data[2]) << 8) |
           static_cast<uint32>(Data[3]);
}

uint64 ReadU64BE(const uint8* Data)
{
    return (static_cast<uint64>(ReadU32BE(Data)) << 32) | ReadU32BE(Data + 4);
}

float ReadFloatBE(const uint8* Data)
{
    const uint32 Bits = ReadU32BE(Data);
    float Value = 0.0f;
    FMemory::Memcpy(&Value, &Bits, sizeof(Value));
    return Value;
}

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
} // namespace SimUdpProtocol

ASimUdpControlledActor::ASimUdpControlledActor()
{
    SetRootComponent(CreateDefaultSubobject<USceneComponent>(TEXT("Root")));
    PrimaryActorTick.bCanEverTick = true;
    PrimaryActorTick.TickGroup = TG_PrePhysics;
}

void ASimUdpControlledActor::BeginPlay()
{
    Super::BeginPlay();

    if (IsValid(ControlledActor))
    {
        InitialControlledTransform = ControlledActor->GetActorTransform();
        const USceneComponent* Root = ControlledActor->GetRootComponent();
        if (Root && Root->Mobility != EComponentMobility::Movable)
        {
            UE_LOG(LogSimUdpBridge, Warning,
                TEXT("Controlled actor '%s' is not Movable"),
                *ControlledActor->GetName());
        }
    }
    else
    {
        UE_LOG(LogSimUdpBridge, Warning,
            TEXT("ControlledActor is unset; commands will be rejected"));
    }

    OpenSocket();
}

void ASimUdpControlledActor::EndPlay(const EEndPlayReason::Type EndPlayReason)
{
    CloseSocket();
    Super::EndPlay(EndPlayReason);
}

void ASimUdpControlledActor::Tick(float DeltaSeconds)
{
    Super::Tick(DeltaSeconds);
    ReceivePackets();
    ApplyPendingPose();
}

bool ASimUdpControlledActor::OpenSocket()
{
    if (ListenPort < 1 || ListenPort > 65535)
    {
        UE_LOG(LogSimUdpBridge, Error, TEXT("ListenPort %d is invalid"), ListenPort);
        return false;
    }

    FIPv4Address Address;
    if (!FIPv4Address::Parse(BindAddress, Address))
    {
        UE_LOG(LogSimUdpBridge, Error, TEXT("Invalid BindAddress '%s'"), *BindAddress);
        return false;
    }

    const FIPv4Endpoint Endpoint(Address, static_cast<uint16>(ListenPort));
    ListenSocket = FUdpSocketBuilder(TEXT("SimUdpBridgeSocket"))
        .AsNonBlocking()
        .BoundToEndpoint(Endpoint)
        .WithReceiveBufferSize(2 * 1024 * 1024)
        .WithSendBufferSize(256 * 1024);

    if (!ListenSocket)
    {
        UE_LOG(LogSimUdpBridge, Error,
            TEXT("Could not bind UDP socket to %s:%d"), *BindAddress, ListenPort);
        return false;
    }

    UE_LOG(LogSimUdpBridge, Display,
        TEXT("Listening for SIL pose packets on %s:%d"), *BindAddress, ListenPort);
    return true;
}

void ASimUdpControlledActor::CloseSocket()
{
    if (!ListenSocket)
    {
        return;
    }

    ListenSocket->Close();
    ISocketSubsystem::Get(PLATFORM_SOCKETSUBSYSTEM)->DestroySocket(ListenSocket);
    ListenSocket = nullptr;
}

void ASimUdpControlledActor::ReceivePackets()
{
    if (!ListenSocket)
    {
        return;
    }

    uint32 PendingDataSize = 0;
    int32 ProcessedPacketCount = 0;
    while (ProcessedPacketCount < SimUdpProtocol::MaxPacketsPerTick &&
           ListenSocket->HasPendingData(PendingDataSize))
    {
        ++ProcessedPacketCount;
        TArray<uint8> Data;
        Data.SetNumUninitialized(
            static_cast<int32>(FMath::Min(PendingDataSize, 65507u)));

        TSharedRef<FInternetAddr> Sender =
            ISocketSubsystem::Get(PLATFORM_SOCKETSUBSYSTEM)->CreateInternetAddr();
        int32 BytesRead = 0;
        if (!ListenSocket->RecvFrom(
                Data.GetData(), Data.Num(), BytesRead, *Sender, ESocketReceiveFlags::None))
        {
            break;
        }

        FPendingPose Candidate;
        if (!ParsePosePacket(Data.GetData(), BytesRead, Candidate))
        {
            ++InvalidPacketCount;
            SendAck(*Sender, 0, 0, SimUdpProtocol::AckInvalidPacket);
            continue;
        }

        ++ValidPacketCount;
        Candidate.Sender = Sender;

        if (!bHasActiveRun || Candidate.RunId != ActiveRunId)
        {
            if (bHasActiveRun && !Candidate.bStartOfRun)
            {
                ++RejectedPacketCount;
                SendAck(*Sender, Candidate.RunId, Candidate.Sequence,
                    SimUdpProtocol::AckRejected);
                continue;
            }

            ActiveRunId = Candidate.RunId;
            bHasActiveRun = true;
            bHasAppliedSequence = false;
            PendingPose.Reset();
            UE_LOG(LogSimUdpBridge, Display,
                TEXT("Started SIL run 0x%016llX"),
                static_cast<unsigned long long>(ActiveRunId));
        }

        if (bHasAppliedSequence && Candidate.Sequence == LastAppliedSequenceRaw)
        {
            // Idempotent retry: the original ACK may have been lost.
            SendAck(*Sender, Candidate.RunId, Candidate.Sequence,
                SimUdpProtocol::AckApplied);
            continue;
        }

        if (!IsNewerThanApplied(Candidate.Sequence) ||
            (PendingPose.IsSet() &&
             static_cast<int32>(Candidate.Sequence - PendingPose->Sequence) <= 0))
        {
            ++RejectedPacketCount;
            SendAck(*Sender, Candidate.RunId, Candidate.Sequence,
                SimUdpProtocol::AckRejected);
            continue;
        }

        PendingPose = MoveTemp(Candidate);
    }
}

bool ASimUdpControlledActor::ParsePosePacket(
    const uint8* Data,
    int32 NumBytes,
    FPendingPose& OutPose) const
{
    using namespace SimUdpProtocol;

    if (NumBytes != CommandPacketSize ||
        FMemory::Memcmp(Data, CommandMagic, 4) != 0)
    {
        return false;
    }

    const uint8 Flags = Data[5];
    if (Data[4] != Version || (Flags & ~KnownCommandFlags) != 0 ||
        ReadU16BE(Data + 6) != 0)
    {
        return false;
    }

    OutPose.bStartOfRun = (Flags & StartRunFlag) != 0;
    OutPose.RunId = ReadU64BE(Data + 8);
    OutPose.Sequence = ReadU32BE(Data + 16);
    OutPose.SimulationTimeNs = ReadU64BE(Data + 20);
    OutPose.PositionMetres = FVector(
        ReadFloatBE(Data + 28),
        ReadFloatBE(Data + 32),
        ReadFloatBE(Data + 36));
    OutPose.Rotation = FQuat(
        ReadFloatBE(Data + 40),
        ReadFloatBE(Data + 44),
        ReadFloatBE(Data + 48),
        ReadFloatBE(Data + 52));

    if (OutPose.RunId == 0 || OutPose.PositionMetres.ContainsNaN() ||
        OutPose.Rotation.ContainsNaN() ||
        OutPose.Rotation.SizeSquared() < UE_SMALL_NUMBER)
    {
        return false;
    }

    OutPose.Rotation.Normalize();
    return true;
}

void ASimUdpControlledActor::ApplyPendingPose()
{
    if (!PendingPose.IsSet())
    {
        return;
    }

    const FPendingPose Pose = MoveTemp(PendingPose.GetValue());
    PendingPose.Reset();

    if (!IsValid(ControlledActor))
    {
        if (Pose.Sender.IsValid())
        {
            SendAck(*Pose.Sender, Pose.RunId, Pose.Sequence,
                SimUdpProtocol::AckApplyFailed);
        }
        return;
    }

    FVector TargetLocation = Pose.PositionMetres * 100.0; // metres to centimetres
    FQuat TargetRotation = Pose.Rotation;

    if (bPositionRelativeToStart)
    {
        TargetLocation += InitialControlledTransform.GetLocation();
    }
    if (bRotationRelativeToStart)
    {
        TargetRotation = InitialControlledTransform.GetRotation() * TargetRotation;
        TargetRotation.Normalize();
    }

    const bool bApplied = ControlledActor->SetActorLocationAndRotation(
        TargetLocation,
        TargetRotation,
        false,
        nullptr,
        ETeleportType::TeleportPhysics);

    if (!bApplied)
    {
        if (Pose.Sender.IsValid())
        {
            SendAck(*Pose.Sender, Pose.RunId, Pose.Sequence,
                SimUdpProtocol::AckApplyFailed);
        }
        return;
    }

    LastAppliedRunId = Pose.RunId;
    LastAppliedSequenceRaw = Pose.Sequence;
    LastAppliedSequence = static_cast<int64>(Pose.Sequence);
    bHasAppliedSequence = true;

    if (Pose.Sender.IsValid())
    {
        SendAck(*Pose.Sender, Pose.RunId, Pose.Sequence,
            SimUdpProtocol::AckApplied);
    }
}

void ASimUdpControlledActor::SendAck(
    const FInternetAddr& Destination,
    uint64 RunId,
    uint32 Sequence,
    uint8 Status)
{
    using namespace SimUdpProtocol;

    if (!bSendAcknowledgements || !ListenSocket)
    {
        return;
    }

    uint8 Ack[AckPacketSize] = {};
    FMemory::Memcpy(Ack, AckMagic, 4);
    Ack[4] = Version;
    Ack[5] = Status;
    WriteU16BE(Ack + 6, 0);
    WriteU64BE(Ack + 8, RunId);
    WriteU32BE(Ack + 16, Sequence);

    int32 BytesSent = 0;
    if (!ListenSocket->SendTo(Ack, AckPacketSize, BytesSent, Destination) ||
        BytesSent != AckPacketSize)
    {
        UE_LOG(LogSimUdpBridge, VeryVerbose, TEXT("Could not send complete ACK"));
    }
}

bool ASimUdpControlledActor::IsNewerThanApplied(uint32 Candidate) const
{
    return !bHasAppliedSequence ||
        static_cast<int32>(Candidate - LastAppliedSequenceRaw) > 0;
}
