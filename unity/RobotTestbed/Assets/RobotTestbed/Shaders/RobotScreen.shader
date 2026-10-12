// Eyes (additive) and chest display (opaque) of the robot: one cell of a luminance atlas, tinted.
// _Cell = (scale u, scale v, offset u, offset v) from robot_config.json > atlasCells (origin bottom-left).
// _Mode 0 = eyes (additive glow, bright core turns white), 1 = display (dark screen, tinted motif).
Shader "CubeEscape/RobotScreen"
{
    Properties
    {
        _Atlas ("Atlas", 2D) = "black" {}
        _Cell ("Cell", Vector) = (0.25, 0.1, 0, 0.9)
        _Tint ("Colour (linear)", Vector) = (0.02, 0.62, 0.95, 0)
        _Glow ("Glow", Float) = 1
        _Mode ("Mode", Float) = 0
        [Enum(UnityEngine.Rendering.BlendMode)] _SrcBlend ("Src", Float) = 1
        [Enum(UnityEngine.Rendering.BlendMode)] _DstBlend ("Dst", Float) = 1
        [Toggle] _ZWrite ("ZWrite", Float) = 0
    }

    SubShader
    {
        Tags { "RenderType" = "Transparent" "Queue" = "Transparent" "RenderPipeline" = "UniversalPipeline" }

        Pass
        {
            Name "Unlit"
            Tags { "LightMode" = "UniversalForward" }
            Blend [_SrcBlend] [_DstBlend]
            ZWrite [_ZWrite]

            HLSLPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Core.hlsl"

            TEXTURE2D(_Atlas); SAMPLER(sampler_Atlas);
            CBUFFER_START(UnityPerMaterial)
                float4 _Atlas_ST, _Cell, _Tint;
                float _Glow, _Mode, _SrcBlend, _DstBlend, _ZWrite;
            CBUFFER_END

            struct Attributes { float4 positionOS : POSITION; float2 uv : TEXCOORD0; };
            struct Varyings { float4 positionCS : SV_POSITION; float2 uv : TEXCOORD0; };

            Varyings vert(Attributes v)
            {
                Varyings o;
                o.positionCS = TransformObjectToHClip(v.positionOS.xyz);
                o.uv = v.uv;
                return o;
            }

            half4 frag(Varyings i) : SV_Target
            {
                float l = SAMPLE_TEXTURE2D(_Atlas, sampler_Atlas, i.uv * _Cell.xy + _Cell.zw).r;
                float3 c;
                if (_Mode < 0.5)
                    c = _Tint.rgb * l * _Glow + pow(l, 6.0) * 0.8 * min(_Glow, 1.5);
                else
                    c = lerp(float3(0.01, 0.025, 0.02), _Tint.rgb * 1.6 * _Glow, l);
                return half4(c, 1.0);
            }
            ENDHLSL
        }
    }
}
