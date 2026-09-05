import { useEffect, useRef } from 'react'
import * as THREE from 'three'

// Woven ribbons represent independent research paths converging on a shared question.
export default function ResearchField({ paused }: { paused: boolean }) {
  const hostRef = useRef<HTMLDivElement>(null)
  const pausedRef = useRef(paused)
  const playbackRef = useRef<() => void>(() => {})
  pausedRef.current = paused

  useEffect(() => {
    const host = hostRef.current
    if (!host) return
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
    const colors = [0xa8ded0, 0x759f91, 0xe5efdc, 0xb2f2bb]

    // A ruled toroidal surface gives the strands a continuous, fabric-like topology.
    const point = (u: number, v: number) => {
      const twist = u * 1.5
      const radius = 2.52 + v * Math.cos(twist)
      return new THREE.Vector3(
        radius * Math.cos(u),
        radius * Math.sin(u) * 1.17,
        v * Math.sin(twist) + Math.sin(u * 2) * 0.45,
      )
    }
    for (let strand = 0; strand < 88; strand++) {
      const v = (strand / 87 - 0.5) * 2.7
      const points = Array.from({ length: 281 }, (_, step) => point((step / 280) * Math.PI * 4, v))
      const geometry = new THREE.BufferGeometry().setFromPoints(points)
      const material = new THREE.LineBasicMaterial({
        color: colors[strand % colors.length],
        transparent: true,
        opacity: strand % 7 === 0 ? 0.76 : 0.33,
      })
      resources.push(geometry, material)
      sculpture.add(new THREE.Line(geometry, material))
    }
    const crossPoints: THREE.Vector3[] = []
    for (let step = 0; step < 180; step++) {
      const u = (step / 180) * Math.PI * 2
      crossPoints.push(point(u, -1.35), point(u, 1.35))
    }
    const crossGeometry = new THREE.BufferGeometry().setFromPoints(crossPoints)
    const crossMaterial = new THREE.LineBasicMaterial({
      color: 0x83b8a7,
      transparent: true,
      opacity: 0.18,
    })
    resources.push(crossGeometry, crossMaterial)
    sculpture.add(new THREE.LineSegments(crossGeometry, crossMaterial))

    const accentGeometry = new THREE.BufferGeometry().setFromPoints(
      Array.from({ length: 361 }, (_, step) => point((step / 360) * Math.PI * 4, 1.43)),
    )
    const accentMaterial = new THREE.LineBasicMaterial({
      color: 0xf0a18d,
      transparent: true,
      opacity: 0.8,
    })
    resources.push(accentGeometry, accentMaterial)
    sculpture.add(new THREE.Line(accentGeometry, accentMaterial))

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
      width = host.clientWidth
      height = host.clientHeight
      camera.aspect = width / Math.max(height, 1)
      camera.updateProjectionMatrix()
      renderer.setSize(width, height)
      const mobile = width < 700
      sculpture.position.set(mobile ? 1.05 : 3.15, mobile ? 2.2 : 0.15, 0)
      sculpture.scale.setScalar(mobile ? 0.48 : 0.92)
      render()
    }
    const render = () => {
      if (contextLost) return
      sculpture.rotation.set(
        0.34 + easedPointer.y * 0.18,
        -0.4 + easedPointer.x * 0.25 + Math.sin(time * 0.1) * 0.18,
        -0.48 + time * 0.025,
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
  }, [])

  useEffect(() => {
    playbackRef.current()
  }, [paused])

  return <div className="research-field" ref={hostRef} aria-hidden="true" />
}
