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

    /** Actor to move. If unset, this UDP bridge actor moves itself. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "SIL UDP")
    TObjectPtr<AActor> ControlledActor;

    /** Local IPv4 address. Leave as 0.0.0.0 to listen on every interface. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "SIL UDP|Network")
    FString BindAddress = TEXT("0.0.0.0");

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "SIL UDP|Network", meta = (ClampMin = "1", ClampMax = "65535"))
    int32 ListenPort = 5005;

    /** Interpret packet positions as metre offsets from the actor's BeginPlay position. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "SIL UDP|Transform")
    bool bPositionRelativeToStart = true;

    /** Interpret packet rotations as offsets from the actor's BeginPlay rotation. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "SIL UDP|Transform")
    bool bRotationRelativeToStart = true;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "SIL UDP|Network")
    bool bSendAcknowledgements = true;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "SIL UDP|Diagnostics")
    int64 LastAppliedSequence = -1;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "SIL UDP|Diagnostics")
    int64 ValidPacketCount = 0;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "SIL UDP|Diagnostics")
    int64 InvalidPacketCount = 0;

protected:
    virtual void BeginPlay() override;
    virtual void EndPlay(const EEndPlayReason::Type EndPlayReason) override;

private:
    struct FPendingPose
    {
        uint32 Sequence = 0;
        uint64 SimulationTimeNs = 0;
        FVector PositionMetres = FVector::ZeroVector;
        FQuat Rotation = FQuat::Identity;
        TSharedPtr<FInternetAddr> Sender;
    };

    bool OpenSocket();
    void CloseSocket();
    void ReceivePackets();
    bool ParsePosePacket(const uint8* Data, int32 NumBytes, FPendingPose& OutPose) const;
    void ApplyPendingPose();
    void SendAck(const FInternetAddr& Destination, uint32 Sequence, uint8 Status);
    bool IsNewerSequence(uint32 Candidate) const;

    FSocket* ListenSocket = nullptr;
    FTransform InitialControlledTransform = FTransform::Identity;
    TOptional<FPendingPose> PendingPose;
    bool bHasAppliedSequence = false;
    uint32 LastAppliedSequenceRaw = 0;
};
