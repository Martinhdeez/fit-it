// Records a clip in the browser. The core transcribes it (POST /fitit/transcribe).

const LIMIT_MS = 60_000;
const MIME_TYPES = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"];

export type Recording = { stop(): void; abort(): void };

export function canRecord(): boolean {
  return typeof MediaRecorder !== "undefined" && navigator.mediaDevices?.getUserMedia !== undefined;
}

export function startRecording(onStop: (blob: Blob) => void, onError: (message: string) => void): Recording {
  let recorder: MediaRecorder | null = null;
  let aborted = false;
  let stopRequested = false;
  let timer = 0;

  navigator.mediaDevices
    .getUserMedia({ audio: true })
    .then((stream) => {
      const release = () => stream.getTracks().forEach((track) => track.stop());
      if (aborted) {
        release();
        return;
      }
      if (stopRequested) {
        release();
        onStop(new Blob());
        return;
      }
      const mime = MIME_TYPES.find((type) => MediaRecorder.isTypeSupported(type));
      recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      const chunks: Blob[] = [];
      recorder.addEventListener("dataavailable", (event) => {
        if (event.data.size > 0) chunks.push(event.data);
      });
      recorder.addEventListener("stop", () => {
        window.clearTimeout(timer);
        release();
        if (!aborted) onStop(new Blob(chunks, { type: recorder?.mimeType || "audio/webm" }));
      });
      recorder.start();
      timer = window.setTimeout(() => recorder?.stop(), LIMIT_MS);
    })
    .catch((error: unknown) => {
      if (aborted) return;
      aborted = true;
      const name = error instanceof DOMException ? error.name : "";
      if (name === "NotAllowedError" || name === "SecurityError") {
        onError("Microphone blocked. Allow it in the browser to dictate.");
      } else if (name === "NotFoundError") {
        onError("No microphone found.");
      } else {
        onError("Could not use the microphone. Try again.");
      }
    });

  return {
    stop() {
      stopRequested = true;
      recorder?.stop();
    },
    abort() {
      aborted = true;
      window.clearTimeout(timer);
      if (recorder && recorder.state !== "inactive") recorder.stop();
    },
  };
}
