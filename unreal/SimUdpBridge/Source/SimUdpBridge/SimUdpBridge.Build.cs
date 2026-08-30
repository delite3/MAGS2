using UnrealBuildTool;

public class SimUdpBridge : ModuleRules
{
    public SimUdpBridge(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;

        PublicDependencyModuleNames.AddRange(
            new string[]
            {
                "Core",
                "CoreUObject",
                "Engine",
                "CesiumRuntime"
            }
        );

        PrivateDependencyModuleNames.AddRange(
            new string[]
            {
                "Networking",
                "Sockets",
                "ImageCore",
                "ImageWrapper"
            }
        );
    }
}
