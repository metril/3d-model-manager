import {
  Box,
  FileCode2,
  FileText,
  Image as ImageIcon,
  type LucideIcon,
  File as FileIcon,
  Video as VideoIcon,
} from "lucide-react";

import type { BlobFormat } from "@/api/types";

/** Short display label per format (Task 8: format badges + placeholder monogram). */
export const FORMAT_LABELS: Record<BlobFormat, string> = {
  stl: "STL",
  "3mf": "3MF",
  obj: "OBJ",
  step: "STEP",
  iges: "IGES",
  gcode_3mf: "G/3MF",
  gcode: "G-code",
  png: "PNG",
  jpg: "JPG",
  webp: "WebP",
  // R13c: doc file kinds (`BlobKind.doc`).
  pdf: "PDF",
  md: "Markdown",
  txt: "Text",
  docx: "Word",
  // P4 camera plan: timelapse video formats (`BlobKind.video`).
  mp4: "MP4",
  avi: "AVI",
  other: "File",
};

/** Icon per format, used for the gallery card's placeholder thumbnail. */
export function formatIcon(format: BlobFormat | undefined): LucideIcon {
  switch (format) {
    case "stl":
    case "obj":
    case "step":
    case "iges":
    case "3mf":
      return Box;
    case "gcode":
    case "gcode_3mf":
      return FileCode2;
    case "png":
    case "jpg":
    case "webp":
      return ImageIcon;
    case "pdf":
    case "md":
    case "txt":
    case "docx":
      return FileText;
    case "mp4":
    case "avi":
      return VideoIcon;
    default:
      return FileIcon;
  }
}
