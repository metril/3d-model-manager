import { useMirrorSelkiesTheme } from "@/lib/selkiesTheme";

/** Renders nothing; mirrors the app theme into the Selkies dashboard's storage key. */
export function SelkiesThemeBridge() {
  useMirrorSelkiesTheme();
  return null;
}
