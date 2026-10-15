import Editor, { loader } from "@monaco-editor/react";
import { useEffect } from "react";
import * as monaco from "monaco-editor/editor/editor.api.js";
import "monaco-editor/languages/features/json/register.js";
import "monaco-editor/editor/common/services/treeViewsDndService.js";
import "monaco-editor/editor/contrib/codelens/browser/codeLensCache.js";
import "monaco-editor/editor/contrib/inlayHints/browser/inlayHintsController.js";
import "monaco-editor/editor/contrib/suggest/browser/suggestMemory.js";
import "monaco-editor/platform/actionWidget/browser/actionWidget.js";
import EditorWorker from "../workers/editor.worker.js?worker&inline";
import JsonWorker from "../workers/json.worker.js?worker&inline";

const FONT_FAMILY = '"JetBrains Mono", ui-monospace, monospace';
const FONT_SIZE = 13;

// Monaco and its workers come from the bundle: no CDN loader, and the workers are inlined since a UMD has no import.meta.url.
window.MonacoEnvironment = {
  getWorker(_workerId: string, label: string): Worker {
    return label === "json" ? new JsonWorker() : new EditorWorker();
  },
};

loader.config({ monaco });
window.__monaco = monaco;

export interface CodeProps {
  value?: string;
  language?: string;
  theme?: string;
  options?: monaco.editor.IStandaloneEditorConstructionOptions;
  onChange?: (value: string) => void;
  className?: string;
}

export function Code({
  value = "",
  language = "plaintext",
  theme = "vs-dark",
  options = {},
  onChange,
  className,
}: CodeProps) {
  useEffect(() => {
    void document.fonts.load(`${FONT_SIZE}px "JetBrains Mono"`).then(() => monaco.editor.remeasureFonts());
  }, []);

  return (
    <div className={className} style={{ width: "100%", height: "100%" }}>
      <Editor
        value={value}
        language={language}
        theme={theme}
        onMount={(editor) => {
          // Monaco's IME proxy is a form field Chrome reports as unnamed; it is never submitted, so a name is all it needs.
          const ime = editor.getDomNode()?.querySelector<HTMLTextAreaElement>(".ime-text-area");
          if (ime) ime.name = "ff-editor-ime";
        }}
        options={{
          minimap: { enabled: false },
          fontSize: FONT_SIZE,
          fontFamily: FONT_FAMILY,
          automaticLayout: true,
          ...options,
        }}
        onChange={(next) => onChange?.(next ?? "")}
      />
    </div>
  );
}
