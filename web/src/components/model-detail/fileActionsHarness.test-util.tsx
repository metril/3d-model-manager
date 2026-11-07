import { useTranslation } from "react-i18next";

import { useFileActionItems } from "@/components/model-detail/useFileActionItems";
import { OverflowMenu } from "@/components/ui/overflow-menu";

/** Test harness: the file group of `FileEntry`'s overflow menu, rendered standalone. */
export function FileActionsMenu(props: Parameters<typeof useFileActionItems>[0]) {
  const { t } = useTranslation();
  const { fileItems, dangerItems, dialogs } = useFileActionItems(props);
  return (
    <>
      <OverflowMenu
        ariaLabel={t("common.moreActionsFor", { name: props.file.rel_path })}
        groups={[{ items: fileItems }, { items: dangerItems }]}
      />
      {dialogs}
    </>
  );
}
