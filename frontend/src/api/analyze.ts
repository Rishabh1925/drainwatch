export type Severity = "normal" | "warning" | "critical";

export type SegmentationClass = {
  name: "structural" | "water" | "waste";
  pixel_count: number;
  percentage?: number;
};

export type AnalysisResponse = {
  inspection_id: string;
  source_id: string;
  asset_id: string;
  captured_at: string;
  blockage_percentage: number;
  severity: Severity;
  confidence: number;
  segmentation: {
    image_url?: string;
    mask_url?: string;
    classes: SegmentationClass[];
  };
  model_version: string;
  evidence?: {
    original_url?: string;
    segmentation_url?: string;
  };
};

export type AnalysisRecord = AnalysisResponse & {
  original_preview_url: string;
  filename: string;
  file_size: number;
  dimensions: { width: number; height: number };
};

export class AnalysisApiError extends Error {
  status?: number;
  reference?: string;

  constructor(message: string, status?: number, reference?: string) {
    super(message);
    this.name = "AnalysisApiError";
    this.status = status;
    this.reference = reference;
  }
}

function messageForStatus(status: number) {
  if (status === 400) return "The analysis request is incomplete. Check the source, asset and image, then try again.";
  if (status === 413) return "The selected frame is too large. Choose a JPG or PNG under 10 MB.";
  if (status === 422) return "The selected file could not be decoded as a valid drainage image.";
  if (status >= 500) return "The analysis service failed while processing the frame. Retry or check System Health.";
  return "The frame could not be analyzed. Review the input and try again.";
}

export async function analyzeFrame({
  image,
  sourceId,
  assetId,
  signal,
}: {
  image?: File;
  sourceId: string;
  assetId: string;
  signal?: AbortSignal;
}): Promise<AnalysisResponse> {
  const body = new FormData();
  if (image) body.append("image", image);
  body.append("source_id", sourceId);
  body.append("asset_id", assetId);

  let response: Response;
  try {
    response = await fetch("/api/analyze", { method: "POST", body, signal });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new AnalysisApiError("Analysis timed out. The frame was not processed. Retry or check System Health.");
    }
    throw new AnalysisApiError("Analysis service unavailable. The frame was not processed. Retry or check System Health.");
  }

  if (!response.ok) {
    let reference: string | undefined;
    let serverMessage: string | undefined;
    try {
      const payload = await response.json();
      reference = payload.error_reference;
      serverMessage = payload.message;
    } catch {
      // The operational fallback below remains actionable when the API returns no JSON.
    }
    throw new AnalysisApiError(serverMessage || messageForStatus(response.status), response.status, reference);
  }

  return response.json() as Promise<AnalysisResponse>;
}
