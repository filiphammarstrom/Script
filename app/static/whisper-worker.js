// Kör KB-Whisper (Kungliga bibliotekets svensktränade Whisper) direkt i webbläsaren
// via Transformers.js. Modellen laddas ner från Hugging Face första gången och cachas
// av webbläsaren – därefter behövs inget nätverk för transkriberingen. WebGPU används
// när det finns, annars WASM (långsammare men funkar överallt). Allt sker privat på
// den egna datorn: ljudet lämnar aldrig webbläsaren.
import { pipeline } from "https://cdn.jsdelivr.net/npm/@huggingface/transformers@3.5.1";

let transcriber = null;
let loadedModel = "";

self.onmessage = async (ev) => {
  const { audio, model, language } = ev.data;
  try {
    if (!transcriber || loadedModel !== model) {
      transcriber = null;
      const device = self.navigator && self.navigator.gpu ? "webgpu" : "wasm";
      transcriber = await pipeline("automatic-speech-recognition", model, {
        device,
        // Kvantiserade vikter: mindre nedladdning och snabbare inferens. WebGPU
        // vill ha fp32-encoder (q4-decoder), WASM klarar q8 rakt igenom.
        dtype: device === "webgpu" ? { encoder_model: "fp32", decoder_model_merged: "q4" } : "q8",
        progress_callback: (p) => {
          if (p.status === "progress") {
            self.postMessage({ type: "progress", file: p.file || "", progress: p.progress || 0 });
          }
        },
      });
      loadedModel = model;
    }
    self.postMessage({ type: "status", message: "Transkriberar i webbläsaren ..." });
    const opts = {
      task: "transcribe",
      chunk_length_s: 30,   // Whisper arbetar i 30-sekundersfönster
      stride_length_s: 5,   // överlapp så ord i skarvarna inte tappas
    };
    if (language) opts.language = language;  // utelämnat = modellen språkdetekterar själv
    const out = await transcriber(audio, opts);
    self.postMessage({ type: "done", text: ((out && out.text) || "").trim() });
  } catch (err) {
    self.postMessage({ type: "error", error: String((err && err.message) || err) });
  }
};
