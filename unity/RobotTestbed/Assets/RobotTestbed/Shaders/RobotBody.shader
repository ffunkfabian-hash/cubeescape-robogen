// Cube Escape robot body (URP, forward). Same formulas as the generator (decorate_skin) and the viewer shader:
// recolour by colour mask -> second armour colour in paint zones -> badges -> glow lines (albedo tint + emission).
// Colours are linear (set with SetVector, never SetColor - that would convert from sRGB a second time).
Shader "CubeEscape/RobotBody"
{
    Properties
    {
        _BaseMap ("Base colour (Classic skin)", 2D) = "white" {}
        _MetallicSmoothness ("Metallic (R) Smoothness (A)", 2D) = "black" {}
        _BumpMap ("Normal", 2D) = "bump" {}
        _OcclusionMap ("Occlusion", 2D) = "white" {}
        _EmissionMap ("Emission (Classic)", 2D) = "black" {}
        _ColorMask ("Colour mask (armour, mechanics, metal)", 2D) = "black" {}
        _LightMask ("Light mask", 2D) = "black" {}
        _BadgeMask ("Badge mask 1", 2D) = "black" {}
        _BadgeMask2 ("Badge mask 2", 2D) = "black" {}
        _LineMask ("Line mask 1", 2D) = "black" {}
        _LineMask2 ("Line mask 2", 2D) = "black" {}
        _ZoneMask ("Zone mask 1", 2D) = "black" {}
        _ZoneMask2 ("Zone mask 2", 2D) = "black" {}
        _LayerMask ("Layer mask 1 (rust, moss, frost, embers)", 2D) = "black" {}
        _LayerMask2 ("Layer mask 2 (camo, wear, grime)", 2D) = "black" {}
        _MaterialMask ("Material mask 1", 2D) = "white" {}
        _MaterialMask2 ("Material mask 2", 2D) = "white" {}
        _Armor ("Armour", Vector) = (0.66, 0.685, 0.72, 0)
        _Armor2 ("Second armour colour", Vector) = (0.66, 0.685, 0.72, 0)
        _Mech ("Mechanics", Vector) = (0.075, 0.08, 0.092, 0)
        _Metal ("Metal", Vector) = (0.38, 0.395, 0.42, 0)
        _Lights ("Lights", Vector) = (1, 0.38, 0.05, 0)
        _Visor ("Visor", Vector) = (0.006, 0.007, 0.01, 0)
        _FabricColor ("Fabric", Vector) = (0.78, 0.1, 0.12, 0)
        _RefA ("Classic luminance: armour, mechanics, metal, lights", Vector) = (0.68, 0.079, 0.39, 0.48)
        _RefB ("Classic luminance: visor, fabric", Vector) = (0.007, 0.71, 0, 0)
        _Finish ("Finish: roughness factor, metal offset", Vector) = (1, 0, 0, 0)
        _ZoneSel0 ("Zones 1-4", Vector) = (0, 0, 0, 0)
        _ZoneSel1 ("Zones 5-8", Vector) = (0, 0, 0, 0)
        _BadgeA0 ("Badge A channels (mask 1)", Vector) = (0, 0, 0, 0)
        _BadgeA1 ("Badge A channels (mask 2)", Vector) = (0, 0, 0, 0)
        _BadgeB0 ("Badge B channels (mask 1)", Vector) = (0, 0, 0, 0)
        _BadgeB1 ("Badge B channels (mask 2)", Vector) = (0, 0, 0, 0)
        _BadgeAColor ("Badge A colour", Vector) = (0, 0, 0, 0)
        _BadgeBColor ("Badge B colour", Vector) = (0, 0, 0, 0)
        _LineSel0 ("Line pattern (mask 1)", Vector) = (0, 0, 0, 0)
        _LineSel1 ("Line pattern (mask 2)", Vector) = (0, 0, 0, 0)
        _LineColor ("Line colour (w = strength)", Vector) = (0, 0, 0, 0)
        _LinePaint ("Line albedo share", Float) = 0.85
        _MaterialSel0 ("Material channel (mask 1)", Vector) = (0, 0, 0, 0)
        _MaterialSel1 ("Material channel (mask 2)", Vector) = (0, 0, 0, 0)
        _MaterialColor ("Material colour (w = on)", Vector) = (0, 0, 0, 0)
        _LayerAmount ("Amount: rust, moss, frost, embers", Vector) = (0, 0, 0, 0)
        _LayerAmount2 ("Amount: camo, -, -, - ; y = cover softness", Vector) = (0, 0.1, 0, 0)
        _RustColor ("Rust", Vector) = (0.25, 0.04, 0.006, 0)
        _MossColor ("Moss", Vector) = (0.07, 0.15, 0.012, 0)
        _FrostColor ("Frost", Vector) = (0.62, 0.82, 1, 0)
        _EmberColor ("Embers", Vector) = (1, 0.11, 0.003, 0)
        _CamoColor ("Camo", Vector) = (0.09, 0.12, 0.028, 0)
        _LayerRough ("Roughness factor: rust, moss, frost, embers", Vector) = (1, 1, 1, 1)
        _LayerMetal ("Metal offset: rust, moss, frost, embers", Vector) = (0, 0, 0, 0)
        _Wear ("Wear (0 clean, 0.5 baked, 1 battered)", Float) = 0.5
        _BareMetal ("Bare metal", Vector) = (0.62, 0.63, 0.65, 0)
        _Glow ("Glow (power x look glow)", Float) = 1
        _EmissionStrength ("Emission strength", Float) = 3
        _OcclusionStrength ("Occlusion strength", Float) = 1
        [Toggle] _Fabric ("Fabric (tinted, no recolour)", Float) = 0
    }

    SubShader
    {
        Tags { "RenderType" = "Opaque" "RenderPipeline" = "UniversalPipeline" "Queue" = "Geometry" }

        Pass
        {
            Name "ForwardLit"
            Tags { "LightMode" = "UniversalForward" }

            HLSLPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #pragma multi_compile _ _MAIN_LIGHT_SHADOWS _MAIN_LIGHT_SHADOWS_CASCADE _MAIN_LIGHT_SHADOWS_SCREEN
            #pragma multi_compile _ _ADDITIONAL_LIGHTS_VERTEX _ADDITIONAL_LIGHTS
            #pragma multi_compile_fragment _ _ADDITIONAL_LIGHT_SHADOWS
            #pragma multi_compile_fragment _ _SHADOWS_SOFT
            #pragma multi_compile_fog

            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Core.hlsl"
            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Lighting.hlsl"

            TEXTURE2D(_BaseMap); SAMPLER(sampler_BaseMap);
            TEXTURE2D(_MetallicSmoothness);
            TEXTURE2D(_BumpMap);
            TEXTURE2D(_OcclusionMap);
            TEXTURE2D(_EmissionMap);
            TEXTURE2D(_ColorMask);
            TEXTURE2D(_LightMask);
            TEXTURE2D(_BadgeMask);
            TEXTURE2D(_BadgeMask2);
            TEXTURE2D(_LineMask);
            TEXTURE2D(_LineMask2);
            TEXTURE2D(_ZoneMask);
            TEXTURE2D(_ZoneMask2);
            TEXTURE2D(_LayerMask);
            TEXTURE2D(_LayerMask2);
            TEXTURE2D(_MaterialMask);
            TEXTURE2D(_MaterialMask2);

            CBUFFER_START(UnityPerMaterial)
                float4 _BaseMap_ST;
                float4 _Armor, _Armor2, _Mech, _Metal, _Lights, _Visor, _FabricColor, _RefA, _RefB, _Finish;
                float4 _ZoneSel0, _ZoneSel1, _BadgeA0, _BadgeA1, _BadgeB0, _BadgeB1, _BadgeAColor, _BadgeBColor;
                float4 _LineSel0, _LineSel1, _LineColor;
                float4 _MaterialSel0, _MaterialSel1, _MaterialColor, _LayerAmount, _LayerAmount2;
                float4 _RustColor, _MossColor, _FrostColor, _EmberColor, _CamoColor, _LayerRough, _LayerMetal, _BareMetal;
                float _LinePaint, _Glow, _EmissionStrength, _OcclusionStrength, _Fabric, _Wear;
            CBUFFER_END

            #define SAMPLE(t, uv) SAMPLE_TEXTURE2D(t, sampler_BaseMap, uv)
            #define COVER(f, a) saturate(((f) - (1.0 - (a))) / _LayerAmount2.y)

            struct Attributes
            {
                float4 positionOS : POSITION;
                float3 normalOS : NORMAL;
                float4 tangentOS : TANGENT;
                float2 uv : TEXCOORD0;
            };

            struct Varyings
            {
                float4 positionCS : SV_POSITION;
                float2 uv : TEXCOORD0;
                float3 positionWS : TEXCOORD1;
                float3 normalWS : TEXCOORD2;
                float4 tangentWS : TEXCOORD3;
                float fogCoord : TEXCOORD4;
            };

            Varyings vert(Attributes v)
            {
                Varyings o;
                VertexPositionInputs p = GetVertexPositionInputs(v.positionOS.xyz);
                VertexNormalInputs n = GetVertexNormalInputs(v.normalOS, v.tangentOS);
                o.positionCS = p.positionCS;
                o.positionWS = p.positionWS;
                o.normalWS = n.normalWS;
                o.tangentWS = float4(n.tangentWS, v.tangentOS.w * GetOddNegativeScale());
                o.uv = v.uv;
                o.fogCoord = ComputeFogFactor(p.positionCS.z);
                return o;
            }

            half4 frag(Varyings i) : SV_Target
            {
                const float3 LUMA = float3(0.2126, 0.7152, 0.0722);
                float2 uv = i.uv;
                float3 c = SAMPLE(_BaseMap, uv).rgb;
                float lum = max(dot(c, LUMA), 1e-5);
                float4 mk = float4(SAMPLE(_ColorMask, uv).rgb, SAMPLE(_LightMask, uv).r);
                float4 ms = SAMPLE(_MetallicSmoothness, uv);
                float rough = 1.0 - ms.a;
                float metal = ms.r;
                float ml = 0.0, ember = 0.0;
                if (_Fabric > 0.5)
                {
                    c = _FabricColor.rgb * (lum / max(_RefB.y, 1e-4));
                    rough = 0.75;
                    metal = 0.0;
                }
                else
                {
                    float vis = saturate(1.0 - (mk.r + mk.g + mk.b + mk.a));
                    c = lerp(c, _Armor.rgb * (lum / _RefA.x), mk.r);
                    c = lerp(c, _Mech.rgb * (lum / _RefA.y), mk.g);
                    c = lerp(c, _Metal.rgb * (lum / _RefA.z), mk.b);
                    c = lerp(c, _Lights.rgb * (lum / _RefA.w), mk.a);
                    c = lerp(c, _Visor.rgb * (lum / max(_RefB.x, 1e-4)), vis);
                    rough = lerp(rough, saturate(rough * _Finish.x), mk.r);
                    metal = lerp(metal, saturate(metal + _Finish.y), mk.r);
                    rough = lerp(rough, lerp(rough, 0.04, 0.8), vis);
                    float ratio = clamp(lum / max(mk.r * _RefA.x + mk.b * _RefA.z, 1e-3), 0.2, 1.6);
                    float ratioAll = clamp(lum / max(mk.r * _RefA.x + mk.g * _RefA.y + mk.b * _RefA.z, 1e-3), 0.2, 1.6);
                    float mz = saturate(dot(SAMPLE(_ZoneMask, uv), _ZoneSel0) + dot(SAMPLE(_ZoneMask2, uv), _ZoneSel1));
                    c = lerp(c, _Armor2.rgb * ratio, mz);
                    float3 region = lerp(mk.r * _Armor.rgb + mk.g * _Mech.rgb + mk.b * _Metal.rgb, _Armor2.rgb, mz);
                    if (_MaterialColor.w > 0.5)
                    {
                        float pm = saturate(dot(SAMPLE(_MaterialMask, uv), _MaterialSel0) + dot(SAMPLE(_MaterialMask2, uv), _MaterialSel1));
                        c = lerp(c, lerp(_MaterialColor.rgb * ratio, c, pm), mk.r);
                    }
                    float4 b0 = SAMPLE(_BadgeMask, uv), b1 = SAMPLE(_BadgeMask2, uv);
                    c = lerp(c, _BadgeAColor.rgb * ratio, saturate(dot(b0, _BadgeA0) + dot(b1, _BadgeA1)));
                    c = lerp(c, _BadgeBColor.rgb * ratio, saturate(dot(b0, _BadgeB0) + dot(b1, _BadgeB1)));
                    float4 lay = SAMPLE(_LayerMask, uv), lay2 = SAMPLE(_LayerMask2, uv);
                    float wearF = lay2.g, grime = lay2.b, patchy = 0.6 + 0.6 * grime;
                    float cv = COVER(lay.r, _LayerAmount.x);
                    c = lerp(c, _RustColor.rgb * patchy * ratioAll, cv);
                    rough = lerp(rough, saturate(rough * _LayerRough.x), cv); metal = lerp(metal, saturate(metal + _LayerMetal.x), cv);
                    cv = COVER(lay.g, _LayerAmount.y);
                    c = lerp(c, _MossColor.rgb * patchy * ratioAll, cv);
                    rough = lerp(rough, saturate(rough * _LayerRough.y), cv); metal = lerp(metal, saturate(metal + _LayerMetal.y), cv);
                    cv = COVER(lay.b, _LayerAmount.z);
                    c = lerp(c, _FrostColor.rgb * patchy * ratioAll, cv);
                    rough = lerp(rough, saturate(rough * _LayerRough.z), cv); metal = lerp(metal, saturate(metal + _LayerMetal.z), cv);
                    c = lerp(c, _CamoColor.rgb * ratioAll, COVER(lay2.r, _LayerAmount2.x));
                    c = lerp(c, _CamoColor.rgb * 0.45 * ratioAll, COVER(lay2.r, _LayerAmount2.x * 0.45));
                    if (_Wear < 0.5)
                    {
                        float clean = (0.5 - _Wear) * 2.0;
                        c = lerp(c, region, saturate(wearF * 1.6) * clean);
                        c = lerp(c, region, grime * clean * 0.4);
                    }
                    else
                    {
                        float more = (_Wear - 0.5) * 2.0;
                        c = lerp(c, _BareMetal.rgb, saturate(wearF * 4.0 * more) * 0.85);
                        c *= 1.0 - 0.6 * more * grime;
                    }
                    float charred = saturate((lay.a - (1.0 - min(1.0, _LayerAmount.w * 2.5))) / _LayerAmount2.y);
                    ember = saturate((lay.a - (1.0 - _LayerAmount.w * 0.3)) / 0.04);
                    c *= 1.0 - 0.88 * charred;
                    ml = saturate(dot(SAMPLE(_LineMask, uv), _LineSel0) + dot(SAMPLE(_LineMask2, uv), _LineSel1));
                    c = c * (1.0 - ml * _LinePaint) + ml * _LinePaint * 0.6 * _LineColor.rgb;
                }
                float3 e = SAMPLE(_EmissionMap, uv).rgb;
                float le = dot(e, LUMA);
                float3 emission = (lerp(e, _Lights.rgb * (le / max(_RefA.w, 1e-4)), mk.a) + _LineColor.rgb * (ml * _LineColor.w)
                                   + _EmberColor.rgb * (ember * 1.5)) * _EmissionStrength * _Glow;

                SurfaceData s = (SurfaceData)0;
                s.albedo = c;
                s.metallic = metal;
                s.smoothness = 1.0 - rough;
                s.normalTS = UnpackNormal(SAMPLE(_BumpMap, uv));
                s.occlusion = lerp(1.0, SAMPLE(_OcclusionMap, uv).r, _OcclusionStrength);
                s.emission = emission;
                s.alpha = 1.0;

                float3 bitangent = i.tangentWS.w * cross(i.normalWS, i.tangentWS.xyz);
                InputData d = (InputData)0;
                d.positionWS = i.positionWS;
                d.normalWS = NormalizeNormalPerPixel(TransformTangentToWorld(s.normalTS,
                    half3x3(i.tangentWS.xyz, bitangent, i.normalWS)));
                d.viewDirectionWS = GetWorldSpaceNormalizeViewDir(i.positionWS);
                d.shadowCoord = TransformWorldToShadowCoord(i.positionWS);
                d.fogCoord = i.fogCoord;
                d.bakedGI = SampleSH(d.normalWS);
                d.normalizedScreenSpaceUV = GetNormalizedScreenSpaceUV(i.positionCS);
                d.shadowMask = half4(1, 1, 1, 1);

                half4 col = UniversalFragmentPBR(d, s);
                col.rgb = MixFog(col.rgb, d.fogCoord);
                return col;
            }
            ENDHLSL
        }

        UsePass "Universal Render Pipeline/Lit/ShadowCaster"
        UsePass "Universal Render Pipeline/Lit/DepthOnly"
        UsePass "Universal Render Pipeline/Lit/DepthNormals"
    }
}
