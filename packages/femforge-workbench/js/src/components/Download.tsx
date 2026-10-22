import { useEffect } from "react";

/** A file the server hands the browser to save, its bytes in base64; each new `seq` saves again. */
export interface DownloadFile {
  seq: number;
  name: string;
  type: string;
  data: string;
}

export interface DownloadProps {
  file?: DownloadFile | null;
  /** Called once the browser has the file, so the server can drop its copy from the state. */
  onDone?: () => void;
}

/** Saves each new file the server hands it to the browser's downloads; renders nothing. */
export function Download({ file, onDone }: DownloadProps) {
  useEffect(() => {
    if (!file?.data) return;

    const bytes = Uint8Array.from(atob(file.data), (char) => char.charCodeAt(0));
    const url = URL.createObjectURL(new Blob([bytes], { type: file.type }));
    const link = document.createElement("a");
    link.href = url;
    link.download = file.name;
    link.click();
    // The browser reads the blob after the click returns.
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
    onDone?.();
    // A file is identified by its seq; a re-render with the same one must not save it again.
  }, [file?.seq]);

  return null;
}
