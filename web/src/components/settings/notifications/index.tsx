import { LogViewer } from "@/components/settings/notifications/LogViewer";
import { NotificationsCard } from "@/components/settings/notifications/NotificationsCard";
import { TemplateEditor } from "@/components/settings/notifications/TemplateEditor";

export function NotificationsPanel() {
  return (
    <div className="space-y-6">
      <NotificationsCard />
      <TemplateEditor />
      <LogViewer />
    </div>
  );
}
