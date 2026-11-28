// Worker source of the edge glow (from ClipDeck, unchanged): a WebGL2 shader draws a soft band of moving colour
// light just inside the rounded edge of the panel (OffscreenCanvas). Messages: init {canvas}, resize {width, height,
// dpr, radius}, state {lit, asleep, motion, divisor}. It fades in with a short overshoot and fades out when unlit.
export const EDGE_GLOW_WORKER = `(function(){"use strict";const G=["#3fe0ff","#4f86ff","#9b6bff","#ff6ec4","#ffa86b","#6ff2d2"],X=\`#version 300 es
in vec2 aPos;
void main() { gl_Position = vec4(aPos, 0.0, 1.0); }\`,$=\`#version 300 es
precision highp float;
uniform vec2 uRes;
uniform float uDpr, uRadius, uTime, uPresence, uReach;
uniform vec3 uLab[6];
out vec4 outColor;

const float PI = 3.14159265;
const float TAU = 6.28318531;
const float LAP = \${3.5.toFixed(3)};
const float WAVE_SPEED = \${1 .toFixed(3)};
const float WAVE_AMP = \${.6.toFixed(3)};
const float BAND = \${10 .toFixed(3)};
const float HALO = \${10 .toFixed(3)};
const float BRIGHT = \${1 .toFixed(3)};
const float SAT = \${1.2.toFixed(3)};

float sdRoundRect(vec2 p, vec2 b, float r) {
  vec2 q = abs(p) - b + r;
  return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - r;
}

// Position along the edge, 0..1 clockwise from the top centre, by arc length of a rounded
// rectangle with corners of radius rr: every point less than rr deep gets a smooth position;
// the seams between sides only start rr deep, and the light is cut off before that.
float edgePosition(vec2 p, vec2 b, float rr) {
  float w = b.x - rr, h = b.y - rr, q = PI * 0.5 * rr;
  float L = 4.0 * (w + h + q);
  vec2 a = abs(p);
  float s;
  if (a.x > w && a.y > h) {
    if (p.x > 0.0 && p.y < 0.0) { vec2 v = p - vec2(w, -h); s = w + rr * atan(v.x, -v.y); }
    else if (p.x > 0.0)        { vec2 v = p - vec2(w, h);  s = w + q + 2.0 * h + rr * atan(v.y, v.x); }
    else if (p.y > 0.0)        { vec2 v = p - vec2(-w, h); s = 3.0 * w + 2.0 * q + 2.0 * h + rr * atan(-v.x, v.y); }
    else                       { vec2 v = p - vec2(-w, -h); s = 3.0 * w + 3.0 * q + 4.0 * h + rr * atan(-v.y, -v.x); }
  } else if (b.x - a.x < b.y - a.y) {
    s = p.x > 0.0 ? w + q + (p.y + h) : 3.0 * w + 3.0 * q + 2.0 * h + (h - p.y);
  } else {
    s = p.y < 0.0 ? p.x : w + 2.0 * q + 2.0 * h + (w - p.x);
  }
  return fract(s / L);
}

vec3 oklabToLinear(vec3 c) {
  float l_ = c.x + 0.3963377774 * c.y + 0.2158037573 * c.z;
  float m_ = c.x - 0.1055613458 * c.y - 0.0638541728 * c.z;
  float s_ = c.x - 0.0894841775 * c.y - 1.2914855480 * c.z;
  float l = l_ * l_ * l_, m = m_ * m_ * m_, s = s_ * s_ * s_;
  return vec3(
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
   -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
   -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s);
}

vec3 palette(float u) {
  float x = fract(u) * 6.0;
  int i = int(floor(x));
  int j = (i + 1) % 6;
  float f = fract(x);
  f = f * f * (3.0 - 2.0 * f);
  vec3 c = mix(uLab[i], uLab[j], f);
  c.yz *= SAT;
  return max(oklabToLinear(c), 0.0);
}

vec3 linearToSrgb(vec3 c) {
  return mix(c * 12.92, 1.055 * pow(c, vec3(1.0 / 2.4)) - 0.055, step(0.0031308, c));
}

float hash(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }

void main() {
  vec2 b = uRes / uDpr * 0.5;
  vec2 p = (gl_FragCoord.xy - uRes * 0.5) / uDpr;
  p.y = -p.y;
  float d = sdRoundRect(p, b, uRadius);
  float coverage = clamp(0.5 - d * uDpr, 0.0, 1.0);
  float e = max(-d, 0.0);
  float rr = min(b.x, b.y) * 0.75;
  // Fixed, not swaying: a swaying cutoff draws a lumpy inner edge. The middle stays clear glass.
  float reach = uReach * (1.0 + 0.06 * sin(TAU * 0.42 * uTime));
  float cutoff = min(HALO * reach * 3.0, rr - 12.0);
  if (coverage <= 0.0 || e >= cutoff) { outColor = vec4(0.0); return; }
  float fade = 1.0 - smoothstep(0.0, cutoff, e);
  fade *= fade;

  float u = edgePosition(p, b, rr);
  float t = uTime * WAVE_SPEED;
  // Whole numbers of waves per lap, so there is no seam where the edge closes.
  float wave = 0.5 * sin(TAU * (2.0 * u + 0.55 * t))
             + 0.32 * sin(TAU * (3.0 * u - 0.80 * t) + 1.7)
             + 0.22 * sin(TAU * (5.0 * u + 1.15 * t) + 0.6)
             + 0.14 * sin(TAU * (7.0 * u - 1.60 * t) + 2.3);
  wave /= 1.18;
  float swell = max(0.22, 1.0 + WAVE_AMP * 1.15 * wave);
  float glint = 0.72 + 0.28 * (0.6 * sin(TAU * (u - 0.31 * t) + 0.9) + 0.4 * sin(TAU * (4.0 * u + 0.67 * t) + 2.1));
  glint *= mix(1.0, swell, 0.35);

  float cu = u - uTime / LAP + 0.035 * sin(TAU * (2.0 * u + 0.17 * uTime));
  vec3 col = palette(cu) * glint;

  float bandW = BAND * swell * reach;
  float haloW = HALO * mix(1.0, swell, 0.7) * reach;
  float core = exp(-e / (1.1 * mix(1.0, swell, 0.4)));
  float band = exp(-pow(e / bandW, 2.0));
  float halo = exp(-e / haloW);
  float haze = exp(-e / (HALO * reach * 1.6));

  // Linear light, tone-mapped: where layers overlap the light turns brighter and whiter.
  vec3 c = col * (1.25 * band + 0.5 * halo + 0.16 * haze) + mix(col, vec3(1.0), 0.7) * core * 1.1;
  c *= BRIGHT * uPresence * fade;
  c = 1.0 - exp(-c * 1.5);

  vec3 s = linearToSrgb(c);
  // Dither: no rings of 8-bit steps in the faint haze.
  s += (hash(gl_FragCoord.xy + fract(uTime * 7.0) * 91.0) - 0.5) / 255.0 * fade;
  s = clamp(s, 0.0, 1.0) * coverage;
  // Premultiplied, alpha = brightest channel: over the dark glass this is the light itself.
  outColor = vec4(s, max(s.r, max(s.g, s.b)));
}\`;function Y(r){const e=Number.parseInt(r.slice(1),16),[a,o,n]=[e>>16&255,e>>8&255,e&255].map(R=>{const p=R/255;return p<=.04045?p/12.92:((p+.055)/1.055)**2.4}),h=Math.cbrt(.4122214708*a+.5363325363*o+.0514459929*n),t=Math.cbrt(.2119034982*a+.6806995451*o+.1073969566*n),f=Math.cbrt(.0883024619*a+.2817188376*o+.6299787005*n);return[.2104542553*h+.793617785*t-.0040720468*f,1.9779984951*h-2.428592205*t+.4505937099*f,.0259040371*h+.7827717662*t-.808675766*f]}function W(r,e,a){const o=r.createShader(e);return o?(r.shaderSource(o,a),r.compileShader(o),r.getShaderParameter(o,r.COMPILE_STATUS)?o:(console.warn("edge glow shader:",r.getShaderInfoLog(o)),r.deleteShader(o),null)):null}function j(r){let e=null;try{e=r.getContext("webgl2",{premultipliedAlpha:!0,antialias:!1,depth:!1,stencil:!1})}catch{return null}if(!e)return null;const a=W(e,e.VERTEX_SHADER,X),o=W(e,e.FRAGMENT_SHADER,$),n=e.createProgram();if(!a||!o||!n)return null;if(e.attachShader(n,a),e.attachShader(n,o),e.linkProgram(n),!e.getProgramParameter(n,e.LINK_STATUS))return console.warn("edge glow program:",e.getProgramInfoLog(n)),null;e.useProgram(n);const h=e.createBuffer();e.bindBuffer(e.ARRAY_BUFFER,h);const t=new Float32Array(48);let f=0,R=0,p=0,B=1,O=0,S=-1;const V=s=>{if(R<1||p<1)return;S=s;const c=R/B,i=p/B,g=10*s*1.06*3+2,T=O>g?(1-Math.SQRT1_2)*O+Math.SQRT1_2*g:g;if(e.bindBuffer(e.ARRAY_BUFFER,h),2*T>=Math.min(c,i)){f=6,t[0]=-1,t[1]=-1,t[2]=1,t[3]=-1,t[4]=1,t[5]=1,t[6]=-1,t[7]=-1,t[8]=1,t[9]=1,t[10]=-1,t[11]=1,e.bufferData(e.ARRAY_BUFFER,t.subarray(0,12),e.DYNAMIC_DRAW);return}const U=-1+2*T/c,I=1-2*T/c,u=-1+2*T/i,d=1-2*T/i;f=24,t[0]=-1,t[1]=d,t[2]=1,t[3]=d,t[4]=1,t[5]=1,t[6]=-1,t[7]=d,t[8]=1,t[9]=1,t[10]=-1,t[11]=1,t[12]=-1,t[13]=-1,t[14]=1,t[15]=-1,t[16]=1,t[17]=u,t[18]=-1,t[19]=-1,t[20]=1,t[21]=u,t[22]=-1,t[23]=u,t[24]=-1,t[25]=u,t[26]=U,t[27]=u,t[28]=U,t[29]=d,t[30]=-1,t[31]=u,t[32]=U,t[33]=d,t[34]=-1,t[35]=d,t[36]=I,t[37]=u,t[38]=1,t[39]=u,t[40]=1,t[41]=d,t[42]=I,t[43]=u,t[44]=1,t[45]=d,t[46]=I,t[47]=d,e.bufferData(e.ARRAY_BUFFER,t,e.DYNAMIC_DRAW)},k=e.getAttribLocation(n,"aPos");e.enableVertexAttribArray(k),e.vertexAttribPointer(k,2,e.FLOAT,!1,0,0);const A=s=>e.getUniformLocation(n,s),v={res:A("uRes"),dpr:A("uDpr"),radius:A("uRadius"),time:A("uTime"),presence:A("uPresence"),reach:A("uReach")};return e.uniform3fv(A("uLab"),new Float32Array(G.flatMap(Y))),e.clearColor(0,0,0,0),{resize(s,c,i,g){(r.width!==s||r.height!==c)&&(r.width=s,r.height=c),R=s,p=c,B=i||1,O=g,e.viewport(0,0,s,c),e.uniform2f(v.res,s,c),e.uniform1f(v.dpr,i),e.uniform1f(v.radius,g),V(S>0?S:1)},draw({time:s,presence:c,reach:i}){e.clear(e.COLOR_BUFFER_BIT),!(c<=0)&&((i!==S||f===0)&&V(i),f!==0&&(e.uniform1f(v.time,s),e.uniform1f(v.presence,c),e.uniform1f(v.reach,i),e.drawArrays(e.TRIANGLES,0,f)))},dispose(){e.deleteBuffer(h),e.deleteProgram(n),e.deleteShader(a),e.deleteShader(o),e.getExtension("WEBGL_lose_context")?.loseContext()}}}const Q=250,K=350,J=450,L=1.35,F=r=>1-(1-r)**3,Z=r=>r<.5?4*r*r*r:1-(-2*r+2)**3/2;let x=null,l=null,b=null,_=0,C=1,H=0,m=!1,N=!1,E=!0,y=performance.now(),w=0,q=y;const D=r=>{const e=r-y;return E?m?w+(1-w)*F(Math.min(1,e/Q)):w*(1-F(Math.min(1,e/K))):m?1:0},ee=r=>{if(!m||w>0||!E)return 1;const e=Math.min(1,(r-y)/J);return e<.45?L*F(e/.45):L-(L-1)*Z((e-.45)/.55)},z=()=>(!l&&x&&(l=j(x),l&&b&&l.resize(...b)),l),M=r=>{if(_=0,!l||!b||N)return;const e=D(r),a=(m||e>0)&&E;if(a&&++H<C){_=requestAnimationFrame(M);return}H=0,l.draw({time:(r-q)/1e3,presence:e,reach:ee(r)}),a&&(_=requestAnimationFrame(M))},P=(r=!1)=>{!_&&(r||m||D(performance.now())>0)&&z()&&(_=requestAnimationFrame(M))};self.onmessage=r=>{const e=r.data;if(e.type==="init"){x=e.canvas,x.addEventListener("contextlost",n=>{n.preventDefault(),l=null}),x.addEventListener("contextrestored",()=>P()),z();return}if(e.type==="resize"){b=[e.width,e.height,e.dpr,e.radius],l?.resize(...b),P();return}if(N=e.asleep,E=e.motion,C=e.divisor,e.lit===m){P();return}const a=performance.now(),o=D(a);m=e.lit,w=o,y=a,m&&o<=0&&(q=a),P(!0)}})();
`
