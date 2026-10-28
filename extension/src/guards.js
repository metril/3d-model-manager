/**
 * True only for a message sent by one of THIS extension's own pages: the
 * sender id must match AND its url must live under the extension's own
 * origin (a content script injected into a web page has our id too, but a
 * web-page url).
 * @param {{id?: string, url?: string}|undefined} sender
 * @param {{id: string, getURL: (path: string) => string}} runtime
 */
export function isOwnExtensionSender(sender, runtime) {
  return (
    Boolean(sender) &&
    sender.id === runtime.id &&
    typeof sender.url === "string" &&
    sender.url.startsWith(runtime.getURL(""))
  );
}
