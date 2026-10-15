import type { Plugin } from "vite";

const WORKER_MANAGER = /monaco-editor[\\/]esm[\\/]vs[\\/]languages[\\/]features[\\/][^\\/]+[\\/]workerManager\.js$/;
const WORKER_FALLBACK = /new Worker\(new URL\('[^']+', import\.meta\.url\), \{ type: "module" \}\)/;
const THROWING_FALLBACK = `(() => { throw new Error("ff: MonacoEnvironment.getWorker must provide this worker"); })()`;

/** Replaces the `new Worker(new URL(...))` fallback Monaco keeps beside MonacoEnvironment.getWorker: a UMD can never load it and vite would emit it as an orphan chunk. */
export function monacoWorkers(): Plugin {
  return {
    name: "ff-monaco-workers",
    enforce: "pre",
    transform(code, id) {
      if (!WORKER_MANAGER.test(id)) return null;

      // A Monaco upgrade that changes the fallback must fail the build rather than bring the orphan chunks back.
      if (!WORKER_FALLBACK.test(code)) this.error(`no worker fallback to replace in ${id}`);

      return { code: code.replace(WORKER_FALLBACK, THROWING_FALLBACK), map: null };
    },
  };
}
