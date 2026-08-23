#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "SimCameraStreamerActor.generated.h"

class ASimUdpControlledActor;
class FSocket;
class USceneCaptureComponent2D;
class USceneComponent;
class UTextureRenderTarget2D;

/**
 * Small SIL camera proof of concept.
 *
 * A SceneCaptureComponent2D renders an RGB image into a transient render
 * target. The image is read back, JPEG-compressed, tagged with the applied
 * pose metadata, and written to one TCP client. The GPU readback and JPEG
 * compression are synchronous in this first POC and are intentionally exposed
 * as diagnostics so their cost can be measured before optimizing the path.
 */
UCLASS(BlueprintType, Blueprintable)
class SIMUDPBRIDGE_API ASimCameraStreamerActor : public AActor
{
    GENERATED_BODY()

public:
    ASimCameraStreamerActor();
    virtual void Tick(float DeltaSeconds) override;
    virtual void OnConstruction(const FTransform& Transform) override;

    /** Actor whose motion the camera follows. Place this sensor visually first. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "SIL Camera|Mount")
    TObjectPtr<AActor> FollowActor = nullptr;

    /** Pose receiver used to correlate each image with an applied SIL command. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "SIL Camera|Timing")
    TObjectPtr<ASimUdpControlledActor> PoseBridge = nullptr;

    /** Suppress images until PoseBridge has successfully applied a command. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "SIL Camera|Timing")
    bool bRequireAppliedPose = false;

    /** Capture at most once for each distinct applied pose sequence. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "SIL Camera|Timing")
    bool bCaptureOnlyOnNewPose = false;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "SIL Camera|Capture", meta = (ClampMin = "16", ClampMax = "3840"))
    int32 ImageWidth = 320;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "SIL Camera|Capture", meta = (ClampMin = "16", ClampMax = "2160"))
    int32 ImageHeight = 240;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "SIL Camera|Capture", meta = (ClampMin = "0.1", ClampMax = "120.0"))
    float CaptureRateHz = 15.0f;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "SIL Camera|Capture", meta = (ClampMin = "1", ClampMax = "100"))
    int32 JpegQuality = 80;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "SIL Camera|Capture", meta = (ClampMin = "5.0", ClampMax = "170.0"))
    float FieldOfViewDegrees = 90.0f;

    /** Local IPv4 address. 0.0.0.0 accepts a client on any interface. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "SIL Camera|Network")
    FString BindAddress = TEXT("0.0.0.0");

    /** Python connects to this TCP port; the pose receiver remains on UDP 5005. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "SIL Camera|Network", meta = (ClampMin = "1", ClampMax = "65535"))
    int32 ListenPort = 5006;

    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "SIL Camera|Network")
    bool bStreamingEnabled = true;

    /** Disconnect a client that makes no send progress for this long. */
    UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "SIL Camera|Network", meta = (ClampMin = "0.25", ClampMax = "30.0"))
    float ClientStallTimeoutSeconds = 2.0f;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "SIL Camera|Components")
    TObjectPtr<USceneCaptureComponent2D> SceneCapture = nullptr;

    UPROPERTY(Transient, VisibleAnywhere, BlueprintReadOnly, Category = "SIL Camera|Components")
    TObjectPtr<UTextureRenderTarget2D> RenderTarget = nullptr;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "SIL Camera|Diagnostics")
    bool bClientConnected = false;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "SIL Camera|Diagnostics")
    int64 CapturedFrameCount = 0;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "SIL Camera|Diagnostics")
    int64 SentFrameCount = 0;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "SIL Camera|Diagnostics")
    int64 SkippedFrameCount = 0;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "SIL Camera|Diagnostics")
    int32 LastJpegBytes = 0;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "SIL Camera|Diagnostics")
    float LastReadbackMilliseconds = 0.0f;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "SIL Camera|Diagnostics")
    float LastJpegMilliseconds = 0.0f;

    /** RGB byte statistics before JPEG compression; useful for blank-frame diagnosis. */
    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "SIL Camera|Diagnostics")
    int32 LastPixelMinimum = 0;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "SIL Camera|Diagnostics")
    float LastPixelMean = 0.0f;

    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "SIL Camera|Diagnostics")
    int32 LastPixelMaximum = 0;

protected:
    virtual void BeginPlay() override;
    virtual void EndPlay(const EEndPlayReason::Type EndPlayReason) override;

private:
    bool InitializeRenderTarget();
    bool OpenListenSocket();
    void AcceptClient();
    void CloseClientSocket();
    void CloseSockets();
    void FlushPendingSend();
    void UpdateCaptureSettings();
    bool CaptureAndQueueFrame();

    FSocket* ListenSocket = nullptr;
    FSocket* ClientSocket = nullptr;
    TArray<uint8> PendingSendBuffer;
    int32 PendingSendOffset = 0;
    double CaptureAccumulatorSeconds = 0.0;
    double StreamStartSeconds = 0.0;
    double LastSendProgressSeconds = 0.0;
    uint64 NextCameraFrameId = 1;
    uint64 StreamerTickId = 0;
    uint64 LastCapturedRunId = 0;
    uint32 LastCapturedPoseSequence = 0;
    bool bHasCapturedPose = false;
    bool bLastStreamingEnabled = false;
    bool bLoggedNonUniformReadback = false;
};
