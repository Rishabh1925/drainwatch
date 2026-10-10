import { useEffect, useRef, useState } from "react";
import { ImagePlus, RefreshCw, Upload, X } from "lucide-react";
import { analyzeFrame, AnalysisApiError, type AnalysisRecord } from "../api/analyze";
import { Button, SeverityLabel, StatusIndicator } from "./ui";

const sourceOptions = [
  { id: "MANUAL", label: "Manual Upload", asset: "" },
  { id: "CCTV-17", label: "CCTV-17 · Drain corridor camera", asset: "D-1042" },
  { id: "CCTV-24", label: "CCTV-24 · Municipal road camera", asset: "D-1098" },
  { id: "DRONE-03", label: "DRONE-03 · Periodic inspection source", asset: "C-0208" },
];

const assetOptions = ["D-1042", "D-1098", "C-0208", "I-0031", "CH-0441", "O-0118"];
const processingStages = ["Uploading frame", "Preparing image", "Running segmentation", "Calculating blockage", "Classifying severity"];

function fileDimensions(file: File): Promise<{ width: number; height: number }> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    const url = URL.createObjectURL(file);
    image.onload = () => {
      resolve({ width: image.naturalWidth, height: image.naturalHeight });
      URL.revokeObjectURL(url);
    };
    image.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("Invalid image"));
    };
    image.src = url;
  });
}

function formatBytes(bytes: number) {
  if (bytes >= 1_000_000) return `${(bytes / 1_000_000).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1_000))} KB`;
}

export function AnalyzeFrame({
  onClose,
  onReview,
}: {
  onClose: () => void;
  onReview: (record: AnalysisRecord) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [sourceId, setSourceId] = useState("MANUAL");
  const [assetId, setAssetId] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState("");
  const [dimensions, setDimensions] = useState({ width: 0, height: 0 });
  const [dragging, setDragging] = useState(false);
  const [processing, setProcessing] = useState(false);
  const [stage, setStage] = useState(0);
  const [error, setError] = useState<AnalysisApiError | null>(null);
  const [result, setResult] = useState<AnalysisRecord | null>(null);

  useEffect(() => {
    if (!processing) return;
    const timer = window.setInterval(() => setStage((current) => Math.min(current + 1, processingStages.length - 1)), 450);
    return () => window.clearInterval(timer);
  }, [processing]);

  const selectSource = (nextSource: string) => {
    setSourceId(nextSource);
    const source = sourceOptions.find((option) => option.id === nextSource);
    if (source?.asset) setAssetId(source.asset);
  };

  const selectFile = async (nextFile?: File) => {
    setError(null);
    if (!nextFile) return;
    if (!["image/jpeg", "image/png"].includes(nextFile.type)) {
      setError(new AnalysisApiError("Unsupported file type. Choose a JPG, JPEG or PNG drainage frame."));
      return;
    }
    if (nextFile.size > 10_000_000) {
      setError(new AnalysisApiError("The selected frame is too large. Choose an image under 10 MB.", 413));
      return;
    }
    try {
      const nextDimensions = await fileDimensions(nextFile);
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      setFile(nextFile);
      setPreviewUrl(URL.createObjectURL(nextFile));
      setDimensions(nextDimensions);
    } catch {
      setError(new AnalysisApiError("The selected file could not be decoded as a valid image.", 422));
    }
  };

  const removeFile = () => {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    setFile(null);
    setPreviewUrl("");
    setDimensions({ width: 0, height: 0 });
  };

  const reset = () => {
    setResult(null);
    setError(null);
    setProcessing(false);
    setStage(0);
    removeFile();
  };

  const submit = async () => {
    setError(null);
    if (sourceId === "MANUAL" && !file) {
      setError(new AnalysisApiError("Select a drainage frame before starting manual analysis."));
      return;
    }
    if (!assetId) {
      setError(new AnalysisApiError("Associate the frame with a monitored asset before analysis."));
      return;
    }
    setProcessing(true);
    setStage(0);
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 30_000);
    try {
      const response = await analyzeFrame({ image: file ?? undefined, sourceId, assetId, signal: controller.signal });
      const originalUrl = previewUrl || response.evidence?.original_url || "";
      setResult({
        ...response,
        original_preview_url: originalUrl,
        filename: file?.name || `${response.source_id}_latest_frame.jpg`,
        file_size: file?.size || 0,
        dimensions,
      });
    } catch (nextError) {
      setError(nextError instanceof AnalysisApiError ? nextError : new AnalysisApiError("Analysis failed. The frame was not processed."));
    } finally {
      window.clearTimeout(timeout);
      setProcessing(false);
    }
  };

  return (
    <div className="analyze-frame-backdrop">
      <section className="analyze-frame-dialog" role="dialog" aria-modal="true" aria-labelledby="analyze-frame-title">
        <header><div><span>DEVELOPMENT / TEST INPUT</span><h2 id="analyze-frame-title">Analyze Frame</h2><p>Submit a drainage frame for DRAINWATCH analysis.</p></div><button onClick={onClose} aria-label="Close frame analysis"><X size={19} /></button></header>
        {!result && <div className="analyze-frame-body">
          <div className="analysis-inputs">
            <label>Source<select value={sourceId} onChange={(event) => selectSource(event.target.value)}>{sourceOptions.map((source) => <option value={source.id} key={source.id}>{source.label}</option>)}</select></label>
            {sourceId !== "MANUAL" && <div className="source-context"><StatusIndicator tone="normal">Source available</StatusIndicator><strong>{sourceId}</strong><span>Associated asset <code>{assetId}</code></span><small>Submit without a file to request the latest captured frame, or select a local frame for test analysis.</small></div>}
            {!file ? <button
              className={`analysis-dropzone ${dragging ? "dragging" : ""}`}
              onClick={() => inputRef.current?.click()}
              onDragEnter={(event) => { event.preventDefault(); setDragging(true); }}
              onDragOver={(event) => event.preventDefault()}
              onDragLeave={() => setDragging(false)}
              onDrop={(event) => { event.preventDefault(); setDragging(false); void selectFile(event.dataTransfer.files[0]); }}
            ><Upload size={22} /><strong>Drop a drainage frame here</strong><span>or choose a file</span><small>JPG · JPEG · PNG · Maximum 10 MB</small></button> : <div className="selected-frame">
              <img src={previewUrl} alt="Selected drainage frame preview" />
              <div><strong>{file.name}</strong><span>{formatBytes(file.size)} · <code>{dimensions.width} × {dimensions.height}</code></span><div><Button variant="quiet" onClick={() => inputRef.current?.click()}>Replace image</Button><Button variant="quiet" onClick={removeFile}>Remove</Button></div></div>
            </div>}
            <input ref={inputRef} type="file" accept=".jpg,.jpeg,.png,image/jpeg,image/png" hidden onChange={(event) => void selectFile(event.target.files?.[0])} />
            <label>Asset<select value={assetId} onChange={(event) => setAssetId(event.target.value)}><option value="">Select monitored asset</option>{assetOptions.map((asset) => <option key={asset}>{asset}</option>)}</select></label>
          </div>
          {processing && <div className="analysis-progress" aria-live="polite"><RefreshCw className="spin" size={18} /><strong>{processingStages[stage]}...</strong><div>{processingStages.map((item, index) => <span className={index < stage ? "done" : index === stage ? "active" : ""} key={item}>{item}</span>)}</div></div>}
          {error && <div className="analysis-error" role="alert"><strong>Analysis failed</strong><p>{error.message}</p>{error.reference && <code>Error reference: {error.reference}</code>}<Button variant="secondary" onClick={() => void submit()}>Retry analysis</Button></div>}
        </div>}
        {result && <div className="analysis-result">
          <div className="analysis-result-media"><figure><figcaption>ORIGINAL FRAME</figcaption>{result.original_preview_url ? <img src={result.original_preview_url} alt="Submitted drainage frame" /> : <div className="result-media-empty"><ImagePlus size={20} /><span>Original evidence retained by analysis service</span></div>}</figure><figure><figcaption>SEGMENTATION OUTPUT</figcaption>{result.segmentation.image_url || result.segmentation.mask_url || result.evidence?.segmentation_url ? <img src={result.segmentation.image_url || result.segmentation.mask_url || result.evidence?.segmentation_url} alt="Segmentation output returned by DRAINWATCH" /> : <div className="result-media-empty"><span>Segmentation image was not included in the API response.</span></div>}</figure></div>
          <aside><span>MODEL-COMPUTED RESULT</span><strong>{result.blockage_percentage.toFixed(1)}<small>%</small></strong><SeverityLabel severity={result.severity} value={`${result.blockage_percentage.toFixed(1)}%`} /><dl><div><dt>Confidence</dt><dd><code>{result.confidence.toFixed(1)}%</code></dd></div><div><dt>Source</dt><dd><code>{result.source_id}</code></dd></div><div><dt>Asset</dt><dd><code>{result.asset_id}</code></dd></div><div><dt>Captured</dt><dd><time>{result.captured_at}</time></dd></div><div><dt>Inspection</dt><dd><code>{result.inspection_id}</code></dd></div><div><dt>Model</dt><dd><code>{result.model_version}</code></dd></div></dl><div className="result-classes">{result.segmentation.classes.map((item) => <span key={item.name}><i className={`class-${item.name}`} />{item.name.replace("structural", "Structural / concrete")}<code>{item.pixel_count.toLocaleString("en-IN")} px</code></span>)}</div><div className="result-verification"><span>Verification</span><StatusIndicator tone="warning">Requires review</StatusIndicator></div></aside>
        </div>}
        <footer>{result ? <><Button onClick={reset}>Analyze Another Frame</Button><Button variant="primary" onClick={() => onReview(result)}>Review Detection</Button></> : <><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={processing} onClick={() => void submit()}>{processing ? "Analysis in progress" : sourceId === "MANUAL" || file ? "Analyze Frame" : "Analyze Latest Frame"}</Button></>}</footer>
      </section>
    </div>
  );
}
