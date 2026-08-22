#include "SimUdpControlledActor.h"

#include "Common/UdpSocketBuilder.h"
#include "IPAddress.h"
#include "Interfaces/IPv4/IPv4Address.h"
#include "Interfaces/IPv4/IPv4Endpoint.h"
#include "SocketSubsystem.h"
#include "Sockets.h"

DEFINE_LOG_CATEGORY_STATIC(LogSimUdpBridge, Log, All);

namespace SimUdpProtocol
{
constexpr int32 PosePacketSize = 48;
constexpr int32 AckPacketSize = 12;
constexpr uint8 Version = 1;
constexpr uint8 PoseMagic[4] = {'S', 'U', 'D', 'P'};
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
} // namespace SimUdpProtocol

ASimUdpControlledActor::ASimUdpControlledActor()
{
    PrimaryActorTick.bCanEverTick = true;
    PrimaryActorTick.TickGroup = TG_PrePhysics;
}

void ASimUdpControlledActor::BeginPlay()
{
    Super::BeginPlay();

    AActor* Target = ControlledActor ? ControlledActor.Get() : this;
    InitialControlledTransform = Target->GetActorTransform();
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
    FIPv4Address Address;
    if (!FIPv4Address::Parse(BindAddress, Address))
    {
        UE_LOG(LogSimUdpBridge, Error, TEXT("Invalid BindAddress '%s'"), *BindAddress);
        return false;
    }

    const FIPv4Endpoint Endpoint(Address, static_cast<uint16>(ListenPort));
    ListenSocket = FUdpSocketBuilder(TEXT("SimUdpBridgeSocket"))
        .AsNonBlocking()
        .AsReusable()
        .BoundToEndpoint(Endpoint)
        .WithReceiveBufferSize(2 * 1024 * 1024)
        .WithSendBufferSize(256 * 1024);

    if (!ListenSocket)
    {
        UE_LOG(LogSimUdpBridge, Error, TEXT("Could not bind UDP socket to %s:%d"), *BindAddress, ListenPort);
        return false;
    }

    UE_LOG(LogSimUdpBridge, Display, TEXT("Listening for SIL pose packets on %s:%d"), *BindAddress, ListenPort);
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
    while (ListenSocket->HasPendingData(PendingDataSize))
    {
        TArray<uint8> Data;
        Data.SetNumUninitialized(FMath::Min(PendingDataSize, 65507u));

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
            SendAck(*Sender, 0, 1);
            continue;
        }

        ++ValidPacketCount;
        if (IsNewerSequence(Candidate.Sequence) &&
            (!PendingPose.IsSet() ||
             static_cast<int32>(Candidate.Sequence - PendingPose->Sequence) > 0))
        {
            Candidate.Sender = Sender;
            PendingPose = MoveTemp(Candidate);
        }
    }
}

bool ASimUdpControlledActor::ParsePosePacket(
    const uint8* Data,
    int32 NumBytes,
    FPendingPose& OutPose) const
{
    using namespace SimUdpProtocol;

    if (NumBytes != PosePacketSize || FMemory::Memcmp(Data, PoseMagic, 4) != 0)
    {
        return false;
    }
    if (Data[4] != Version || Data[5] != 0 || ReadU16BE(Data + 6) != 0)
    {
        return false;
    }

    OutPose.Sequence = ReadU32BE(Data + 8);
    OutPose.SimulationTimeNs = ReadU64BE(Data + 12);
    OutPose.PositionMetres = FVector(
        ReadFloatBE(Data + 20),
        ReadFloatBE(Data + 24),
        ReadFloatBE(Data + 28));
    OutPose.Rotation = FQuat(
        ReadFloatBE(Data + 32),
        ReadFloatBE(Data + 36),
        ReadFloatBE(Data + 40),
        ReadFloatBE(Data + 44));

    if (OutPose.PositionMetres.ContainsNaN() || OutPose.Rotation.ContainsNaN() ||
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

    AActor* Target = ControlledActor ? ControlledActor.Get() : this;
    FVector TargetLocation = Pose.PositionMetres * 100.0; // metres to Unreal centimetres
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

    Target->SetActorLocationAndRotation(
        TargetLocation,
        TargetRotation,
        false,
        nullptr,
        ETeleportType::TeleportPhysics);

    LastAppliedSequenceRaw = Pose.Sequence;
    LastAppliedSequence = static_cast<int64>(Pose.Sequence);
    bHasAppliedSequence = true;

    if (Pose.Sender.IsValid())
    {
        SendAck(*Pose.Sender, Pose.Sequence, 0);
    }
}

void ASimUdpControlledActor::SendAck(
    const FInternetAddr& Destination,
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
    WriteU32BE(Ack + 8, Sequence);

    int32 BytesSent = 0;
    ListenSocket->SendTo(Ack, AckPacketSize, BytesSent, Destination);
}

bool ASimUdpControlledActor::IsNewerSequence(uint32 Candidate) const
{
    return !bHasAppliedSequence || static_cast<int32>(Candidate - LastAppliedSequenceRaw) > 0;
}
