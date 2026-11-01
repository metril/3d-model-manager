import { useFollowSelkiesTheme, useMirrorSelkiesTheme } from "@/lib/selkiesTheme";

/** Renders nothing; mirrors the app theme into the Selkies dashboard's storage key and
 * follows the dashboard's own toggle. */
export function SelkiesThemeBridge() {
  useMirrorSelkiesTheme();
  useFollowSelkiesTheme();
  return null;
}
