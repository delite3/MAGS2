#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "SimUdpControlledActor.generated.h"

class FInternetAddr;
class FSocket;

UCLASS(BlueprintType, Blueprintable)
class SIMUDPBRIDGE_API ASimUdpControlledActor : public AActor
{
    GENERATED_BODY()

public:
    ASimUdpControlledActor();
    virtual void Tick(float DeltaSeconds) override;

    /**
     * Return metadata for the most recently applied pose. This is deliberately
     * a C++-only method because Unreal's Blueprint reflection does not support
     * uint64 properties reliably. Camera sensors use it to tag an image with
     * the exact SIL command that produced the rendered vehicle state.
     */
    bool GetLastAppliedPoseMetadata(
        uint64& OutRunId,
        uint32& OutSequence,
        uint64& OutSimulationTimeNs) const;

    /** Existing level actor that receives the commanded transform. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "SIL UDP")
    TObjectPtr<AActor> ControlledActor = nullptr;

    /** Local IPv4 address. 0.0.0.0 listens on every interface. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "SIL UDP|Network")
    FString BindAddress = TEXT("0.0.0.0");

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "SIL UDP|Network", meta = (ClampMin = "1", ClampMax = "65535"))
    int32 ListenPort = 5005;

    /** Packet positions are metre offsets from the target's BeginPlay position. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "SIL UDP|Transform")
    bool bPositionRelativeToStart = true;

    /** Packet rotations are offsets from the target's BeginPlay rotation. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "SIL UDP|Transform")
    bool bRotationRelativeToStart = true;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "SIL UDP|Network")
    bool bSendAcknowledgements = true;

    // uint64 is valid C++/reflection data but is not a Blueprint-supported
    // property type. VisibleAnywhere keeps it readable in the Details panel.
    UPROPERTY(VisibleAnywhere, Category = "SIL UDP|Diagnostics")
    uint64 LastAppliedRunId = 0;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "SIL UDP|Diagnostics")
    int64 LastAppliedSequence = -1;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "SIL UDP|Diagnostics")
    int64 ValidPacketCount = 0;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "SIL UDP|Diagnostics")
    int64 InvalidPacketCount = 0;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "SIL UDP|Diagnostics")
    int64 RejectedPacketCount = 0;

protected:
    virtual void BeginPlay() override;
    virtual void EndPlay(const EEndPlayReason::Type EndPlayReason) override;

private:
    struct FPendingPose
    {
        uint64 RunId = 0;
        uint32 Sequence = 0;
        uint64 SimulationTimeNs = 0;
        FVector PositionMetres = FVector::ZeroVector;
        FQuat Rotation = FQuat::Identity;
        bool bStartOfRun = false;
        TSharedPtr<FInternetAddr> Sender;
    };

    bool OpenSocket();
    void CloseSocket();
    void ReceivePackets();
    bool ParsePosePacket(const uint8* Data, int32 NumBytes, FPendingPose& OutPose) const;
    void ApplyPendingPose();
    void SendAck(
        const FInternetAddr& Destination,
        uint64 RunId,
        uint32 Sequence,
        uint8 Status);
    bool IsNewerThanApplied(uint32 Candidate) const;

    FSocket* ListenSocket = nullptr;
    FTransform InitialControlledTransform = FTransform::Identity;
    TOptional<FPendingPose> PendingPose;
    bool bHasActiveRun = false;
    uint64 ActiveRunId = 0;
    bool bHasAppliedSequence = false;
    uint32 LastAppliedSequenceRaw = 0;
    uint64 LastAppliedSimulationTimeNsRaw = 0;
};
