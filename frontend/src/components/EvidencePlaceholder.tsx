// Evidence slots render a representative drain frame behind the label. The live analysis
// flow shows the real uploaded/stored image instead (see Inspections' EvidenceViewer); this
// is the fallback for seeded records and empty slots, so the UI never shows a blank box.
// The sample is chosen deterministically from the source id, so a given camera always maps
// to the same frame and different cameras show different ones.
import sample1 from "../../Pictures/ITO.webp";
import sample2 from "../../Pictures/photo.webp";
import sample3 from "../../Pictures/117063860.avif";
import sample4 from "../../Pictures/thequint_2018-01_d507a634-c3b3-4921-93a2-2519fb2ba97f_c725ccaa-b5eb-4234-8c92-991862603976.webp";

const SAMPLES = [sample1, sample2, sample3, sample4];

function pickSample(seed: string): string {
  let hash = 0;
  for (let i = 0; i < seed.length; i += 1) hash = (hash * 31 + seed.charCodeAt(i)) >>> 0;
  return SAMPLES[hash % SAMPLES.length];
}

export function EvidencePlaceholder({
  sourceId,
  label,
  compact = false,
  image,
}: {
  sourceId: string;
  label: string;
  compact?: boolean;
  image?: string;
}) {
  const src = image ?? pickSample(sourceId);
  return (
    <div className={`evidence-placeholder has-sample ${compact ? "compact" : ""}`} role="img" aria-label={`${label} for ${sourceId}`}>
      <img className="evidence-sample" src={src} alt="" aria-hidden="true" loading="lazy" />
      <span>PROJECT EVIDENCE</span>
      <strong>{label}</strong>
      <code>{sourceId}</code>
    </div>
  );
}
