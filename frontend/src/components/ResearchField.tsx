import { useEffect, useRef } from 'react'
import * as THREE from 'three'

const ribbonVertexShader = `
  uniform float uTime;
  uniform float uPhase;
  uniform float uWidth;
  varying vec2 vUv;
  varying vec3 vNormal;
  varying vec3 vView;

  vec3 ribbonPoint(float u, float v) {
    float phase = uPhase + sin(uTime * 0.22) * 0.16;
    float angle = u * 5.4 + phase;
    float taper = pow(max(sin(u * 3.14159265), 0.0), 0.7);
    float width = (v - 0.5) * uWidth * taper;
    float twist = u * 4.3 + phase + sin(uTime * 0.18) * 0.24;
    return vec3(
      (u - 0.5) * 8.0,
      sin(angle) * 0.85 + (u - 0.5) * 0.5 + cos(twist) * width,
      cos(angle * 0.8) * 0.5 + sin(twist) * width
    );
  }

  void main() {
    vUv = uv;
    vec3 p = ribbonPoint(uv.x, uv.y);
    vec3 tangent = ribbonPoint(min(uv.x + 0.001, 1.0), uv.y)
      - ribbonPoint(max(uv.x - 0.001, 0.0), uv.y);
    vec3 across = ribbonPoint(uv.x, 1.0) - ribbonPoint(uv.x, 0.0);
    vNormal = normalize(normalMatrix * cross(tangent, across) + vec3(0.00001));
    vec4 viewPosition = modelViewMatrix * vec4(p, 1.0);
    vView = normalize(-viewPosition.xyz);
    gl_Position = projectionMatrix * viewPosition;
  }
`

const ribbonFragmentShader = `
  uniform vec3 uColor;
  uniform float uOpacity;
  varying vec2 vUv;
  varying vec3 vNormal;
  varying vec3 vView;

  void main() {
    vec3 normal = normalize(vNormal) * (gl_FrontFacing ? 1.0 : -1.0);
    vec3 light = normalize(vec3(-0.4, 0.8, 1.6));
    float diffuse = abs(dot(normal, light));
    float sheen = pow(abs(dot(normal, normalize(light + vView))), 28.0);
    float edge = pow(abs(vUv.y - 0.5) * 2.0, 12.0);
    float silk = 0.98 + 0.02 * sin(vUv.y * 280.0);
    vec3 color = uColor * (0.34 + diffuse * 0.62) * silk;
    color += vec3(0.7, 0.86, 0.78) * (sheen * 0.48 + edge * 0.16);
    float tip = smoothstep(0.0, 0.07, vUv.x) * smoothstep(0.0, 0.07, 1.0 - vUv.x);
    gl_FragColor = vec4(color, (0.68 + edge * 0.24) * tip * uOpacity);
    #include <tonemapping_fragment>
    #include <colorspace_fragment>
  }
`

const atmosphereFragmentShader = `
  uniform float uTime;
  varying vec2 vUv;

  void main() {
    float sweep = vUv.x * 0.8 + vUv.y * 0.35;
    float fold = sin(vUv.y * 3.5 + uTime * 0.12) * 0.08;
    float cool = pow(max(0.0, cos((sweep + fold - uTime * 0.018) * 7.0)), 16.0);
    float warm = pow(max(0.0, cos((sweep - fold + uTime * 0.012) * 5.0 + 2.4)), 24.0);
    float grain = fract(sin(dot(gl_FragCoord.xy, vec2(12.9898, 78.233))) * 43758.5453);
    vec3 color = mix(vec3(0.18, 0.72, 0.57), vec3(0.85, 0.65, 0.4), warm * 0.7);
    float alpha = (cool * 0.11 + warm * 0.055) * (0.9 + grain * 0.1);
    gl_FragColor = vec4(color, alpha);
    #include <colorspace_fragment>
  }
`

export default function ResearchField({
  paused,
  variant = 'welcome',
}: {
  paused: boolean
  variant?: 'welcome' | 'workspace'
}) {
  const hostRef = useRef<HTMLDivElement>(null)
  const pausedRef = useRef(paused)
  const playbackRef = useRef<() => void>(() => {})
  pausedRef.current = paused

  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    if (typeof window.WebGLRenderingContext === 'undefined') {
      host.dataset.state = 'fallback'
      return
    }
    let renderer: THREE.WebGLRenderer
    try {
      renderer = new THREE.WebGLRenderer({
        antialias: true,
        alpha: true,
        powerPreference: 'low-power',
      })
    } catch {
      host.dataset.state = 'fallback'
      return
    }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.6))
    renderer.setClearColor(0x111715, 0)
    renderer.domElement.setAttribute('aria-hidden', 'true')
    host.appendChild(renderer.domElement)

    const scene = new THREE.Scene()
    const camera = new THREE.PerspectiveCamera(35, 1, 0.1, 60)
    camera.position.z = 17
    const sculpture = new THREE.Group()
    scene.add(sculpture)
    const resources: (THREE.BufferGeometry | THREE.Material)[] = []
    const timeUniform = { value: 0 }
    const opacityUniform = { value: 1 }
    const ribbonGeometry = new THREE.PlaneGeometry(1, 1, 160, 12)
    resources.push(ribbonGeometry)
    const ribbons = [
      { color: 0x91d8bd, phase: 1.4, width: 0.85, offset: -0.2 },
      { color: 0xc8f1dd, phase: -0.35, width: 1.15, offset: 0.22 },
      { color: 0xe3bd89, phase: 0.5, width: 0.055, offset: 0.3 },
    ]
    ribbons.forEach(({ color, phase, width, offset }, index) => {
      const material = new THREE.ShaderMaterial({
        uniforms: {
          uTime: timeUniform,
          uOpacity: opacityUniform,
          uColor: { value: new THREE.Color(color) },
          uPhase: { value: phase },
          uWidth: { value: width },
        },
        vertexShader: ribbonVertexShader,
        fragmentShader: ribbonFragmentShader,
        transparent: true,
        side: THREE.DoubleSide,
        depthWrite: false,
      })
      resources.push(material)
      const ribbon = new THREE.Mesh(ribbonGeometry, material)
      ribbon.position.set(0, offset, index * 0.12)
      ribbon.renderOrder = index + 1
      ribbon.frustumCulled = false
      sculpture.add(ribbon)
    })

    // Clip-space light bands cover the viewport independently of the ribbons.
    const atmosphereGeometry = new THREE.PlaneGeometry(2, 2)
    const atmosphereMaterial = new THREE.ShaderMaterial({
      uniforms: { uTime: timeUniform },
      vertexShader: `
        varying vec2 vUv;
        void main() {
          vUv = uv;
          gl_Position = vec4(position.xy, 0.0, 1.0);
        }
      `,
      fragmentShader: atmosphereFragmentShader,
      transparent: true,
      depthTest: false,
      depthWrite: false,
    })
    const atmosphere = new THREE.Mesh(atmosphereGeometry, atmosphereMaterial)
    atmosphere.frustumCulled = false
    atmosphere.renderOrder = -1
    scene.add(atmosphere)
    resources.push(atmosphereGeometry, atmosphereMaterial)

    let width = 1
    let height = 1
    let inView = true
    let contextLost = false
    let frame = 0
    let time = 0
    let lastTime = 0
    const pointer = new THREE.Vector2()
    const easedPointer = new THREE.Vector2()
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)')
    const resize = () => {
      width = Math.max(host.clientWidth, 1)
      height = Math.max(host.clientHeight, 1)
      camera.aspect = width / Math.max(height, 1)
      camera.updateProjectionMatrix()
      renderer.setSize(width, height)
      const mobile = width < 700
      opacityUniform.value = mobile && variant === 'workspace' ? 0.22 : 1
      if (variant === 'workspace') {
        const viewHeight =
          2 * Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)) * camera.position.z
        const viewWidth = viewHeight * camera.aspect
        const size = Math.min(width * (mobile ? 0.72 : 0.3), 400)
        sculpture.position.set(viewWidth * 0.28, viewHeight * (0.5 - 200 / height), 0)
        sculpture.scale.setScalar(((size / height) * viewHeight) / 8)
      } else {
        sculpture.position.set(mobile ? 1.05 : 3.15, mobile ? 2.2 : 0.15, 0)
        sculpture.scale.setScalar(mobile ? 0.48 : 0.9)
      }
      render()
    }
    const render = () => {
      if (contextLost) return
      timeUniform.value = time
      sculpture.rotation.set(
        0.12 + easedPointer.y * 0.14,
        -0.12 + easedPointer.x * 0.18,
        0.24 + Math.sin(time * 0.16) * 0.035,
      )
      renderer.render(scene, camera)
      host.dataset.state = 'ready'
    }
    const tick = (now: number) => {
      frame = 0
      if (contextLost || document.hidden || !inView || pausedRef.current || reducedMotion.matches)
        return
      const delta = Math.min((now - lastTime) / 1000, 0.05)
      lastTime = now
      time += delta
      easedPointer.lerp(pointer, 1 - Math.exp(-delta * 3.5))
      render()
      frame = requestAnimationFrame(tick)
    }
    const resume = () => {
      if (
        !contextLost &&
        !frame &&
        inView &&
        !document.hidden &&
        !pausedRef.current &&
        !reducedMotion.matches
      ) {
        lastTime = performance.now()
        frame = requestAnimationFrame(tick)
      }
    }
    playbackRef.current = resume
    reducedMotion.addEventListener('change', resume)
    const onPointer = (event: PointerEvent) => {
      const bounds = host.getBoundingClientRect()
      pointer.set(
        (event.clientX - bounds.left) / width - 0.5,
        (event.clientY - bounds.top) / height - 0.5,
      )
    }
    const onLeave = () => pointer.set(0, 0)
    const onContextLost = (event: Event) => {
      event.preventDefault()
      contextLost = true
      host.dataset.state = 'fallback'
      cancelAnimationFrame(frame)
      frame = 0
    }
    const onContextRestored = () => {
      contextLost = false
      render()
      resume()
    }
    const observer = new ResizeObserver(resize)
    observer.observe(host)
    const visibilityObserver = new IntersectionObserver(([entry]) => {
      inView = entry.isIntersecting
      resume()
    })
    visibilityObserver.observe(host)
    const surface = host.parentElement
    surface?.addEventListener('pointermove', onPointer)
    surface?.addEventListener('pointerleave', onLeave)
    document.addEventListener('visibilitychange', resume)
    renderer.domElement.addEventListener('webglcontextlost', onContextLost)
    renderer.domElement.addEventListener('webglcontextrestored', onContextRestored)
    resize()
    resume()
    return () => {
      cancelAnimationFrame(frame)
      playbackRef.current = () => {}
      reducedMotion.removeEventListener('change', resume)
      renderer.domElement.removeEventListener('webglcontextlost', onContextLost)
      renderer.domElement.removeEventListener('webglcontextrestored', onContextRestored)
      observer.disconnect()
      visibilityObserver.disconnect()
      document.removeEventListener('visibilitychange', resume)
      surface?.removeEventListener('pointermove', onPointer)
      surface?.removeEventListener('pointerleave', onLeave)
      resources.forEach((resource) => resource.dispose())
      renderer.dispose()
      renderer.domElement.remove()
    }
  }, [variant])

  useEffect(() => {
    playbackRef.current()
  }, [paused])

  return (
    <div
      className={variant === 'workspace' ? 'research-field workspace-field' : 'research-field'}
      ref={hostRef}
      aria-hidden="true"
    />
  )
}
