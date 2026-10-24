// Browser speech recognition (Web Speech API). Chrome sends the audio to Google for transcription.

type RecognitionEvent = { results: ArrayLike<ArrayLike<{ transcript: string }>> };

type Recognition = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((event: RecognitionEvent) => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
};

type RecognitionClass = new () => Recognition;

function recognitionClass(): RecognitionClass | undefined {
  const scope = window as unknown as {
    SpeechRecognition?: RecognitionClass;
    webkitSpeechRecognition?: RecognitionClass;
  };
  return scope.SpeechRecognition ?? scope.webkitSpeechRecognition;
}

export function canDictate(): boolean {
  return recognitionClass() !== undefined;
}

export type Dictation = { stop(): void; abort(): void };

// onText gets the whole transcript so far; onEnd gets the error code, or null.
export function startDictation(onText: (text: string) => void, onEnd: (error: string | null) => void): Dictation {
  const Recognition = recognitionClass();
  if (!Recognition) throw new Error("Speech recognition is not supported");
  const recognition = new Recognition();
  recognition.lang = navigator.language;
  recognition.continuous = true;
  recognition.interimResults = true;

  let error: string | null = null;
  let aborted = false;
  recognition.onresult = (event) => {
    if (aborted) return;
    let text = "";
    for (let index = 0; index < event.results.length; index++) text += event.results[index][0].transcript;
    onText(text);
  };
  recognition.onerror = (event) => {
    error = event.error;
  };
  recognition.onend = () => onEnd(aborted ? null : error);
  recognition.start();

  return {
    stop: () => recognition.stop(),
    abort: () => {
      aborted = true;
      recognition.abort();
    },
  };
}

export function dictationError(code: string): string | null {
  switch (code) {
    case "aborted":
    case "no-speech":
      return null;
    case "not-allowed":
    case "service-not-allowed":
      return "Microphone blocked. Allow it in the browser to dictate.";
    case "audio-capture":
      return "No microphone found.";
    case "network":
      return "Dictation needs an internet connection.";
    default:
      return "Dictation stopped. Try again.";
  }
}
