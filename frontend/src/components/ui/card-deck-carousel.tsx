import * as React from "react"

/*
 * Card Deck Carousel — photos stacked like a loose deck of prints. Throw the
 * top card in any direction (drag and let go) and it flies off, then slides
 * back in at the bottom of the pile; the next one is already waiting. The
 * buttons, arrow keys and autoplay do the same throw; "previous" pulls the
 * last card back on top.
 *
 * Slides without an `image` get a painted landscape, so it works with no
 * assets at all.
 */

export type LandPalette = "dawn" | "alpine" | "dusk" | "mist"

export type Slide = {
  image?: string
  title?: string
  caption?: string
  alt?: string
  /** Palette and seed of the painted landscape used when `image` is empty. */
  palette?: LandPalette
  seed?: number
}

export type CardDeckCarouselProps = {
  slides?: Slide[]
  /** Any CSS length. Default 100svh. */
  height?: number | string
  /** Width of a card, any CSS length. */
  cardWidth?: string
  /** CSS aspect-ratio of a card. */
  aspect?: string
  /** How messy the pile is: the largest tilt in degrees of the cards behind. */
  scatter?: number
  /** ms per card; 0 turns autoplay off. It waits while someone is interacting. */
  autoplay?: number
  background?: string
  ink?: string
  onChange?: (index: number) => void
  className?: string
  style?: React.CSSProperties
  ariaLabel?: string
}

// #region logic
export function wrap(i: number, n: number): number {
  return n ? ((i % n) + n) % n : 0
}

/** Resting pose of a card `depth` places down the pile. Only the top four show. */
export function deckPose(depth: number, tilt: number, reduce = false) {
  const d = Math.min(depth, 3)
  return {
    y: d ? -d * 16 : 0,
    scale: 1 - d * 0.055,
    rot: depth === 0 || reduce ? 0 : tilt,
    opacity: depth > 3 ? 0 : 1,
    shade: 1 - d * 0.07,
  }
}

/** A stable, slightly random tilt per card, in [-max, max] degrees. */
export function tiltFor(i: number, max: number): number {
  const u = (((i + 1) * 2654435761) >>> 0) / 4294967296
  return (u * 2 - 1) * max
}

/** Did the drag end as a throw? dx in px, vx in px/ms, w = card width. */
export function isThrow(dx: number, vx: number, w: number): boolean {
  return Math.abs(dx) > w * 0.28 || (Math.abs(vx) > 0.55 && Math.abs(dx) > 24)
}

export function pad2(n: number): string {
  return n < 10 ? "0" + n : String(n)
}
// #endregion logic

/* ------------------------------------------------------- painted images */

const PALETTES = {
  dawn: { top: "#e7b7a5", bottom: "#f8e8d6", sun: "#fff4df", far: "#d2b2bb", near: "#3a2a3b", mist: "255,240,232" },
  alpine: { top: "#7ea5c8", bottom: "#e3ecf2", sun: "#ffffff", far: "#a9bfd0", near: "#1c3044", mist: "236,244,250" },
  dusk: { top: "#2a2450", bottom: "#ef8d60", sun: "#ffd9a6", far: "#93607c", near: "#18121f", mist: "255,196,160" },
  mist: { top: "#c4d0cb", bottom: "#eef1ec", sun: "#ffffff", far: "#aebcb5", near: "#2c3a33", mist: "246,248,245" },
}

function mulberry32(seed: number): () => number {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}
function hexRgb(h: string): [number, number, number] {
  const v = parseInt(h.replace("#", ""), 16)
  return [(v >> 16) & 255, (v >> 8) & 255, v & 255]
}
function mixRgb(a: string, b: string, t: number): string {
  const A = hexRgb(a)
  const B = hexRgb(b)
  return "rgb(" + A.map((v, i) => Math.round(v + (B[i] - v) * t)).join(",") + ")"
}

// A layered-ridge landscape: sky, a low sun, five ridges fading into haze with
// mist between them, a tree line on the nearest, film grain. Painted once.
function paintLandscape(seed: number, palette: LandPalette, w = 1600, h = 1000): string {
  if (typeof document === "undefined") return ""
  const c = document.createElement("canvas")
  c.width = w
  c.height = h
  const g = c.getContext("2d")
  if (!g) return ""
  const P = PALETTES[palette] ?? PALETTES.dawn
  const r = mulberry32(seed * 104729 + 7)

  const sky = g.createLinearGradient(0, 0, 0, h * 0.72)
  sky.addColorStop(0, P.top)
  sky.addColorStop(1, P.bottom)
  g.fillStyle = sky
  g.fillRect(0, 0, w, h)
  const sx = w * (0.22 + r() * 0.56)
  const sy = h * (0.26 + r() * 0.16)
  const halo = g.createRadialGradient(sx, sy, 0, sx, sy, w * 0.45)
  halo.addColorStop(0, "rgba(" + hexRgb(P.sun).join(",") + ",.85)")
  halo.addColorStop(0.08, "rgba(" + hexRgb(P.sun).join(",") + ",.55)")
  halo.addColorStop(1, "rgba(" + hexRgb(P.sun).join(",") + ",0)")
  g.fillStyle = halo
  g.fillRect(0, 0, w, h)
  g.fillStyle = P.sun
  g.beginPath()
  g.arc(sx, sy, h * 0.045, 0, Math.PI * 2)
  g.fill()

  const layers = 5
  for (let L = 0; L < layers; L++) {
    const k = L / (layers - 1)
    const base = h * (0.42 + k * 0.4)
    const amp = h * (0.07 + k * 0.1)
    const ph = [r(), r(), r(), r()].map((v) => v * Math.PI * 2)
    const fr = [1.3 + r(), 3.1 + r() * 2, 7 + r() * 4, 17 + r() * 8]
    const ridge = (x: number) => {
      const u = x / w
      return (
        base -
        amp *
          (0.55 * Math.sin(u * fr[0] + ph[0]) +
            0.28 * Math.sin(u * fr[1] + ph[1]) +
            0.12 * Math.abs(Math.sin(u * fr[2] + ph[2])) +
            0.05 * Math.sin(u * fr[3] + ph[3]))
      )
    }
    const mist = g.createLinearGradient(0, base - amp * 1.4, 0, base + amp * 0.4)
    mist.addColorStop(0, "rgba(" + P.mist + ",0)")
    mist.addColorStop(1, "rgba(" + P.mist + "," + (0.55 - k * 0.35).toFixed(2) + ")")
    g.fillStyle = mist
    g.fillRect(0, base - amp * 1.4, w, amp * 1.8)
    const body = g.createLinearGradient(0, base - amp, 0, h)
    body.addColorStop(0, mixRgb(P.far, P.near, Math.pow(k, 1.3)))
    body.addColorStop(1, mixRgb(P.far, P.near, Math.min(1, Math.pow(k, 1.3) + 0.18)))
    g.fillStyle = body
    g.beginPath()
    g.moveTo(0, h)
    for (let x = 0; x <= w; x += 6) g.lineTo(x, ridge(x))
    g.lineTo(w, h)
    g.closePath()
    g.fill()
    if (L >= layers - 2) {
      g.fillStyle = mixRgb(P.far, P.near, Math.min(1, Math.pow(k, 1.3) + 0.08))
      for (let x = 0; x < w; x += 7 + r() * 9) {
        if (r() < 0.35) continue
        const y = ridge(x) + 2
        const th = h * (0.025 + r() * 0.035) * (0.6 + k)
        const tw = th * 0.32
        g.beginPath()
        g.moveTo(x, y - th)
        g.lineTo(x + tw, y)
        g.lineTo(x - tw, y)
        g.closePath()
        g.fill()
      }
    }
  }

  const vig = g.createRadialGradient(w / 2, h * 0.45, h * 0.3, w / 2, h / 2, w * 0.78)
  vig.addColorStop(0, "rgba(0,0,0,0)")
  vig.addColorStop(1, "rgba(0,0,0,.32)")
  g.fillStyle = vig
  g.fillRect(0, 0, w, h)
  const grain = g.getImageData(0, 0, w, h)
  const d = grain.data
  for (let i = 0; i < d.length; i += 4) {
    const v = (r() - 0.5) * 14
    d[i] += v
    d[i + 1] += v
    d[i + 2] += v
  }
  g.putImageData(grain, 0, 0)
  return c.toDataURL("image/jpeg", 0.88)
}

const DEFAULT_SLIDES: Slide[] = [
  { title: "First Light", caption: "Haze lifting off the eastern ridges.", palette: "dawn", seed: 3 },
  { title: "High Pass", caption: "Cold air, clear to the far range.", palette: "alpine", seed: 8 },
  { title: "Ember Hour", caption: "The last of the sun on the valley floor.", palette: "dusk", seed: 14 },
  { title: "Still Valley", caption: "Morning mist that never quite lifts.", palette: "mist", seed: 21 },
  { title: "Rose Ridge", caption: "Five ridges, one long exhale.", palette: "dawn", seed: 34 },
  { title: "Blue Hour", caption: "Pines going dark against the snow.", palette: "alpine", seed: 55 },
]

// Every slide as an image URL; slides without one are painted after mount.
function useSlideImages(slides: Slide[]): string[] {
  const key = slides.map((s) => s.image || (s.palette || "dawn") + ":" + (s.seed ?? 1)).join("|")
  const [painted, setPainted] = React.useState(() => slides.map(() => ""))
  React.useEffect(() => {
    setPainted(slides.map((s, i) => (s.image ? "" : paintLandscape(s.seed ?? i + 1, s.palette || "dawn"))))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])
  return slides.map((s, i) => s.image || painted[i] || "")
}

const CD_CSS = [
  ".cd-root{position:relative;width:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:clamp(28px,5vh,52px);overflow:hidden;background:var(--cd-bg);color:var(--cd-ink);outline:none;padding:24px 16px;box-sizing:border-box}",
  ".cd-root:focus-visible{box-shadow:inset 0 0 0 2px var(--cd-ink)}",
  ".cd-deck{position:relative;width:var(--cd-w);aspect-ratio:var(--cd-aspect);max-height:calc(var(--cd-h) * .62);max-width:calc(var(--cd-h) * .62 * var(--cd-ar));touch-action:pan-y}",
  ".cd-card{position:absolute;inset:0;border-radius:18px;overflow:hidden;background:#d9d5cc;box-shadow:0 1px 2px rgba(0,0,0,.08),0 30px 60px -30px rgba(0,0,0,.45);transition:transform .6s cubic-bezier(.2,.8,.2,1),opacity .4s ease,filter .4s ease;will-change:transform}",
  ".cd-card[data-top='1']{cursor:grab}",
  ".cd-card[data-drag='1']{cursor:grabbing;transition:none}",
  ".cd-card[data-fly='1']{transition:transform .45s cubic-bezier(.3,.5,.3,1),opacity .45s ease}",
  ".cd-card[data-snap='1']{transition:none}",
  ".cd-card img{position:absolute;inset:0;width:100%;height:100%;max-width:none;object-fit:cover;display:block;pointer-events:none;user-select:none}",
  ".cd-meta{display:flex;align-items:center;gap:clamp(16px,3vw,32px);width:min(var(--cd-w),100%);max-width:560px}",
  ".cd-cap{flex:1;min-width:0;overflow:hidden;padding-bottom:.2em}",
  ".cd-cap>*{display:block;animation:cd-in .6s cubic-bezier(.2,.8,.2,1) both}",
  ".cd-title{font:500 clamp(18px,2.2vw,24px)/1.15 ui-serif,Georgia,'Times New Roman',serif;letter-spacing:-.01em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}",
  ".cd-sub{margin-top:4px;font:400 13px/1.4 ui-sans-serif,system-ui,sans-serif;color:var(--cd-muted);animation-delay:.05s}",
  ".cd-count{font:500 12px/1 ui-monospace,SFMono-Regular,Menlo,monospace;letter-spacing:.14em;color:var(--cd-muted)}",
  ".cd-count b{color:var(--cd-ink);font-weight:500}",
  ".cd-btn{appearance:none;border:1px solid var(--cd-line);background:transparent;color:inherit;width:40px;height:40px;border-radius:50%;display:grid;place-items:center;cursor:pointer;flex:none;transition:background .2s ease,color .2s ease}",
  ".cd-btn:hover{background:var(--cd-ink);color:var(--cd-bg)}",
  ".cd-btn:focus-visible{outline:2px solid var(--cd-ink);outline-offset:2px}",
  ".cd-sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}",
  "@keyframes cd-in{from{transform:translateY(100%);opacity:0}to{transform:none;opacity:1}}",
  "@media (prefers-reduced-motion:reduce){.cd-card,.cd-card[data-fly='1']{transition:opacity .3s ease}.cd-cap>*{animation:none}}",
].join("\n")

type Override = { i: number; x: number; y: number; r: number; mode: "drag" | "fly" | "snap" }

export default function CardDeckCarousel({
  slides = DEFAULT_SLIDES,
  height = "100svh",
  cardWidth = "min(78vw, 440px)",
  aspect = "4 / 5",
  scatter = 6,
  autoplay = 4200,
  background = "var(--color-background, #efece6)",
  ink = "var(--color-foreground, #161513)",
  onChange,
  className,
  style,
  ariaLabel = "Image carousel",
}: CardDeckCarouselProps) {
  const n = slides.length
  const srcs = useSlideImages(slides)
  const [top, setTop] = React.useState(0)
  const [over, setOver] = React.useState(null as Override | null)
  const rootRef = React.useRef(null as HTMLDivElement | null)
  const deckRef = React.useRef(null as HTMLDivElement | null)
  const busy = React.useRef(false)
  const lastTouch = React.useRef(0)
  const topRef = React.useRef(0)
  topRef.current = top
  const reduce = React.useMemo(() => typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches, [])

  const later = (fn: () => void, ms: number) => window.setTimeout(fn, ms)

  // Fly the top card off toward (dirX, dirY), then tuck it under the pile.
  const throwTop = React.useCallback(
    (dirX: number, fromX = 0, fromY = 0, dirY = 0) => {
      const w = deckRef.current?.offsetWidth || 400
      if (busy.current || n < 2) return
      busy.current = true
      const i = topRef.current
      const sx = dirX >= 0 ? 1 : -1
      setOver({ i, x: fromX + sx * w * 1.25, y: fromY + dirY * 0.4 - 40, r: sx * 16, mode: "fly" })
      later(() => {
        setOver(null)
        setTop((t) => wrap(t + 1, n))
        busy.current = false
      }, reduce ? 200 : 430)
    },
    [n, reduce],
  )

  // Pull the bottom card back on top from the left.
  const pullBack = React.useCallback(() => {
    const w = deckRef.current?.offsetWidth || 400
    if (busy.current || n < 2) return
    busy.current = true
    const i = wrap(topRef.current - 1, n)
    setOver({ i, x: -w * 1.25, y: -40, r: -16, mode: "snap" })
    requestAnimationFrame(() =>
      requestAnimationFrame(() => {
        setOver({ i, x: 0, y: 0, r: 0, mode: "fly" })
        later(() => {
          setOver(null)
          setTop(i)
          busy.current = false
        }, reduce ? 200 : 450)
      }),
    )
  }, [n, reduce])

  React.useEffect(() => {
    onChange?.(top)
  }, [top, onChange])

  React.useEffect(() => {
    if (!autoplay || n < 2) return
    const root = rootRef.current
    let seen = true
    const io = new IntersectionObserver(([e]) => (seen = e.isIntersecting))
    if (root) io.observe(root)
    const t = window.setInterval(() => {
      if (!seen || document.hidden || performance.now() - lastTouch.current < autoplay) return
      throwTop(1)
    }, autoplay)
    return () => {
      window.clearInterval(t)
      io.disconnect()
    }
  }, [autoplay, n, throwTop])

  // dragging the top card
  const drag = React.useRef(null as null | { x: number; y: number; lx: number; lt: number; vx: number })
  const onDown = (e: React.PointerEvent) => {
    lastTouch.current = performance.now()
    if (busy.current || (e.pointerType === "mouse" && e.button !== 0)) return
    ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
    drag.current = { x: e.clientX, y: e.clientY, lx: e.clientX, lt: e.timeStamp, vx: 0 }
  }
  const onMove = (e: React.PointerEvent) => {
    const d = drag.current
    if (!d) return
    const dt = Math.max(1, e.timeStamp - d.lt)
    d.vx = 0.8 * ((e.clientX - d.lx) / dt) + 0.2 * d.vx
    d.lx = e.clientX
    d.lt = e.timeStamp
    const dx = e.clientX - d.x
    const dy = e.clientY - d.y
    setOver({ i: topRef.current, x: dx, y: dy, r: reduce ? 0 : dx * 0.05, mode: "drag" })
  }
  const onUp = (e: React.PointerEvent) => {
    const d = drag.current
    drag.current = null
    lastTouch.current = performance.now()
    if (!d) return
    const dx = e.clientX - d.x
    const dy = e.clientY - d.y
    const w = deckRef.current?.offsetWidth || 400
    if (isThrow(dx, d.vx, w)) throwTop(dx, dx, dy, dy)
    else setOver(null)
  }
  const onKey = (e: React.KeyboardEvent) => {
    lastTouch.current = performance.now()
    if (e.key === "ArrowRight") throwTop(1)
    else if (e.key === "ArrowLeft") pullBack()
    else return
    e.preventDefault()
  }

  const s = slides[top] || {}

  return (
    <div
      ref={rootRef}
      className={["cd-root", className].filter(Boolean).join(" ")}
      style={{
        height,
        ["--cd-h" as string]: typeof height === "number" ? height + "px" : height,
        ["--cd-w" as string]: cardWidth,
        ["--cd-aspect" as string]: aspect,
        ["--cd-ar" as string]: aspect,
        ["--cd-bg" as string]: background,
        ["--cd-ink" as string]: ink,
        ["--cd-muted" as string]: "color-mix(in srgb, var(--cd-ink) 55%, transparent)",
        ["--cd-line" as string]: "color-mix(in srgb, var(--cd-ink) 18%, transparent)",
        ...style,
      }}
      role="region"
      aria-roledescription="carousel"
      aria-label={ariaLabel}
      tabIndex={0}
      onKeyDown={onKey}
    >
      <style>{CD_CSS}</style>
      <div ref={deckRef} className="cd-deck">
        {slides.map((sl, i) => {
          const depth = wrap(i - top, n)
          const p = deckPose(depth, tiltFor(i, scatter), reduce)
          const o = over && over.i === i ? over : null
          const isTop = depth === 0
          const transform = o
            ? "translate(" + o.x + "px," + o.y + "px) rotate(" + o.r + "deg)"
            : "translateY(" + p.y + "px) scale(" + p.scale + ") rotate(" + p.rot.toFixed(2) + "deg)"
          return (
            <div
              key={i}
              className="cd-card"
              data-top={isTop ? "1" : "0"}
              data-drag={o?.mode === "drag" ? "1" : "0"}
              data-fly={o?.mode === "fly" ? "1" : "0"}
              data-snap={o?.mode === "snap" ? "1" : "0"}
              style={{
                transform,
                zIndex: o ? n + 1 : n - depth,
                opacity: o ? 1 : p.opacity,
                filter: o || isTop ? "none" : "brightness(" + p.shade + ")",
              }}
              aria-hidden={isTop ? undefined : true}
              onPointerDown={isTop ? onDown : undefined}
              onPointerMove={isTop ? onMove : undefined}
              onPointerUp={isTop ? onUp : undefined}
              onPointerCancel={isTop ? onUp : undefined}
            >
              {srcs[i] ? <img src={srcs[i]} alt={isTop ? sl.alt || sl.title || "" : ""} draggable={false} /> : null}
            </div>
          )
        })}
      </div>
      <div className="cd-meta">
        <button type="button" className="cd-btn" aria-label="Previous" onClick={() => ((lastTouch.current = performance.now()), pullBack())}>
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <path d="M10 3 5 8l5 5" stroke="currentColor" strokeWidth="1.4" />
          </svg>
        </button>
        <div className="cd-cap" key={top} aria-hidden="true">
          {s.title ? <span className="cd-title">{s.title}</span> : null}
          {s.caption ? <span className="cd-sub">{s.caption}</span> : null}
        </div>
        <span className="cd-count" aria-hidden="true">
          <b>{pad2(top + 1)}</b> / {pad2(n)}
        </span>
        <button type="button" className="cd-btn" aria-label="Next" onClick={() => ((lastTouch.current = performance.now()), throwTop(1))}>
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <path d="m6 3 5 5-5 5" stroke="currentColor" strokeWidth="1.4" />
          </svg>
        </button>
      </div>
      <div className="cd-sr" aria-live="polite">
        {"Card " + (top + 1) + " of " + n + (s.title ? ": " + s.title : "")}
      </div>
    </div>
  )
}
