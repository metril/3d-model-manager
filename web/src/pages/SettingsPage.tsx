import { DryingSettingsCard } from "@/components/settings/DryingSettingsCard";
import { ScheduledDryingsList } from "@/components/drying/ScheduledDryingsList";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";

import { useAuth } from "@/api/auth";
import { ApiError } from "@/api/client";
import { useStorageBackends } from "@/api/settings";
import { useTranslation } from "react-i18next";
import { AppearanceCard } from "@/components/settings/AppearanceCard";
import { AutomationCard } from "@/components/settings/AutomationCard";
import { BambuAccountCard } from "@/components/settings/BambuAccountCard";
import { ArchivePurgeCard } from "@/components/settings/ArchivePurgeCard";
import { ConfirmOutcomeCard } from "@/components/settings/ConfirmOutcomeCard";
import { BackupsCard } from "@/components/settings/BackupsCard";
import { UpdatesCard } from "@/components/settings/UpdatesCard";
import { BrowserExtensionCard } from "@/components/settings/BrowserExtensionCard";
import { CategoriesSection } from "@/components/settings/CategoriesSection";
import { ChangePasswordCard } from "@/components/settings/ChangePasswordCard";
import { FailureDetectionCard } from "@/components/settings/FailureDetectionCard";
import { ExternalLinksCard } from "@/components/settings/ExternalLinksCard";
import { GroupsCard } from "@/components/settings/GroupsCard";
import { HomeAssistantCard } from "@/components/settings/HomeAssistantCard";
import { NotificationsPanel } from "@/components/settings/notifications";
import { OidcProvidersCard } from "@/components/settings/OidcProvidersCard";
import { VirtualPrinterCard } from "@/components/settings/VirtualPrinterCard";
import { PrinterEnabledCard } from "@/components/settings/PrinterEnabledCard";
import { PrinterSetupCard } from "@/components/settings/PrinterSetupCard";
import { PrintablesAccountCard } from "@/components/settings/PrintablesAccountCard";
import { PrintCostCard } from "@/components/settings/PrintCostCard";
import { ScanReport } from "@/components/settings/ScanReport";
import { SiteTokensCard } from "@/components/settings/SiteTokensCard";
import { SlicerIntegrationCard } from "@/components/settings/SlicerIntegrationCard";
import { SpoolmanCard } from "@/components/settings/SpoolmanCard";
import { StorageBackendsCard } from "@/components/settings/StorageBackendsCard";
import { ServerTab } from "@/components/settings/server/ServerTab";
import { SecurityCard } from "@/components/settings/SecurityCard";
import { TwoFactorCard } from "@/components/settings/TwoFactorCard";
import { UsersCard } from "@/components/settings/UsersCard";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { PageContainer } from "@/components/ui/page-container";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

export function SettingsPage() {
  // The single multi-backend `StorageBackendsCard` is now the ONLY storage UI
  // (the legacy single-backend config card was removed in M8 F); the page
  // shell gates on that same list.
  const backendsQuery = useStorageBackends();
  const auth = useAuth();
  const { t } = useTranslation();
  const canSettings = auth.can("settings:write");
  const isAdmin = auth.can("admin");
  const search = useSearch({ strict: false }) as { tab?: string };
  const navigate = useNavigate();

  return (
    <PageContainer width="default">
      <div>
        <h1 className="text-lg font-semibold text-foreground">{t("settings.title")}</h1>
        <p className="text-sm text-muted-foreground">
          {t("settings.subtitle")}
        </p>
      </div>

      {backendsQuery.isLoading ? (
        <Skeleton className="h-72 w-full rounded-xl" />
      ) : backendsQuery.isError ? (
        <Card className="mx-auto mt-12 max-w-md">
          <CardHeader className="items-center text-center">
            <CardTitle>{t("settings.loadError")}</CardTitle>
            <CardDescription>
              {backendsQuery.error instanceof ApiError ? backendsQuery.error.detail : "Something went wrong."}
            </CardDescription>
          </CardHeader>
          <div className="flex justify-center pb-4">
            <Button type="button" onClick={() => void backendsQuery.refetch()}>
              {t("settings.retry")}
            </Button>
          </div>
        </Card>
      ) : (
        <Tabs
          value={search.tab ?? "general"}
          onValueChange={(v) => void navigate({ to: "/settings", search: { tab: v }, replace: true })}
        >
          <TabsList>
            <TabsTrigger value="general">{t("settings.tabs.general")}</TabsTrigger>
            {canSettings && <TabsTrigger value="storage">{t("settings.tabs.storage")}</TabsTrigger>}
            {canSettings && <TabsTrigger value="printer">{t("settings.tabs.printer")}</TabsTrigger>}
            {canSettings && (
              <TabsTrigger value="notifications">{t("settings.tabs.notifications")}</TabsTrigger>
            )}
            <TabsTrigger value="accounts">{t("settings.tabs.accounts")}</TabsTrigger>
            {isAdmin && <TabsTrigger value="access">{t("settings.tabs.access")}</TabsTrigger>}
            {isAdmin && <TabsTrigger value="server">{t("settings.tabs.server")}</TabsTrigger>}
          </TabsList>
          <TabsContent value="general" className="space-y-6">
            <AppearanceCard />
            {canSettings && <ExternalLinksCard />}
            {canSettings && <UpdatesCard />}
            <AutomationCard />
            <CategoriesSection />
            <PrintCostCard />
            {auth.can("inventory:write") && <SpoolmanCard />}
            {auth.data?.has_password && <ChangePasswordCard />}
            <TwoFactorCard />
          </TabsContent>
          {canSettings && (
            <TabsContent value="storage" className="space-y-6">
              <StorageBackendsCard />
              <BackupsCard />
              <ArchivePurgeCard />
              <ScanReport />
            </TabsContent>
          )}
          {canSettings && (
            <TabsContent value="printer" className="space-y-6">
              <PrinterEnabledCard />
              <PrinterSetupCard />
              <FailureDetectionCard />
              <ConfirmOutcomeCard />
              <HomeAssistantCard />
              <VirtualPrinterCard />
              <DryingSettingsCard />
              <ScheduledDryingsList />
            </TabsContent>
          )}
          {canSettings && (
            <TabsContent value="notifications" className="space-y-6">
              <NotificationsPanel />
            </TabsContent>
          )}
          <TabsContent value="accounts" className="space-y-6">
            {/* Signpost, kept OUTSIDE the cards grid below: users connect an
                account here and then look for its collections nearby. They
                live on their own page, so say so -- and adding this as a 5th
                grid item would have broken the packing worked out below. */}
            <p className="text-sm text-muted-foreground">
              Connect your gallery accounts here. The collections they hold appear under{" "}
              <Link to="/collections" className="underline">
                Collections
              </Link>
              , where you pick which ones to keep synced.
            </p>
            <div className="grid gap-6 xl:grid-cols-2">
              {/* Sparse grid auto-placement never backfills: a col-span-2 card
                  only avoids stranding a cell if it starts an EVEN number of
                  single-column cards in. Placing the Bambu login form (two
                  columns wide: email/region + password) right after the
                  first pair keeps every row packed. Round 8 T6 added
                  SlicerIntegrationCard as a FOURTH single-column card
                  (BrowserExtensionCard was the third and used to trail alone
                  in an odd tail row) -- four singles + one double now pack
                  into exactly three full rows with no orphan cell:
                  [SiteTokens, Printables] / [Bambu (span 2)] /
                  [BrowserExtension, SlicerIntegration]. Any future
                  single-column card added here would go back to an odd tail
                  unless paired with another single or the double is moved. */}
              <SiteTokensCard />
              <PrintablesAccountCard />
              <BambuAccountCard className="xl:col-span-2" />
              <BrowserExtensionCard />
              <SlicerIntegrationCard />
            </div>
          </TabsContent>
          {isAdmin && (
            <TabsContent value="access" className="space-y-6">
              <SecurityCard />
              <UsersCard />
              <GroupsCard />
              <OidcProvidersCard />
            </TabsContent>
          )}
          {isAdmin && (
            <TabsContent value="server" className="space-y-6">
              <ServerTab />
            </TabsContent>
          )}
        </Tabs>
      )}
    </PageContainer>
  );
}
