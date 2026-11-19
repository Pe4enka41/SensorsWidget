#version 440
layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;

layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    vec2 tileSize;
    float radius;
    float lens;
    float hover;
    vec2 lightPos;
    float darkMode;
};

layout(binding = 1) uniform sampler2D srcTex;

float sdRoundedBox(vec2 p, vec2 b, float r) {
    vec2 q = abs(p) - b + r;
    return min(max(q.x, q.y), 0.0) + length(max(q, 0.0)) - r;
}

void main() {
    vec2 p = qt_TexCoord0 * tileSize;
    vec2 b = tileSize * 0.5;
    float r = min(radius, min(b.x, b.y));
    float d = sdRoundedBox(p - b, b - 0.5, r);

    float shape = 1.0 - smoothstep(-1.0, 1.0, d);
    if (shape < 0.004) {
        discard;
    }

    // The backdrop already arrives blurred, the way desktop acrylic does.
    vec3 col = texture(srcTex, qt_TexCoord0).rgb;

    // Acrylic lifts saturation a little so the desktop stays recognisable.
    float lum = dot(col, vec3(0.299, 0.587, 0.114));
    col = mix(vec3(lum), col, 1.16);

    if (darkMode > 0.5) {
        col = mix(col, vec3(0.270, 0.278, 0.288), 0.40);
        col *= 1.02;
    } else {
        col = mix(col, vec3(0.949, 0.951, 0.956), 0.60);
        col *= 1.02;
    }

    // Very soft light falling from the top edge.
    float sheen = 1.0 - clamp(p.y / tileSize.y, 0.0, 1.0);
    col += sheen * sheen * 0.018 * (darkMode > 0.5 ? 1.0 : 0.4);

    // One-pixel inner hairline, like the system panels.
    float line = smoothstep(0.0, 0.9, -d) * (1.0 - smoothstep(0.9, 2.1, -d));
    vec3 rim = darkMode > 0.5 ? vec3(1.0) : vec3(0.0);
    col = mix(col, rim, line * (darkMode > 0.5 ? 0.10 : 0.07));

    fragColor = vec4(col, 1.0) * shape * qt_Opacity;
}

