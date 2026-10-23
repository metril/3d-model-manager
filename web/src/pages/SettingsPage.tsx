import { Link } from "@tanstack/react-router";

import { useAuth } from "@/api/auth";
import { ApiError } from "@/api/client";
import { useStorageBackends } from "@/api/settings";
import { AutomationCard } from "@/components/settings/AutomationCard";
import { BambuAccountCard } from "@/components/settings/BambuAccountCard";
import { BrowserExtensionCard } from "@/components/settings/BrowserExtensionCard";
import { CategoriesSection } from "@/components/settings/CategoriesSection";
import { ChangePasswordCard } from "@/components/settings/ChangePasswordCard";
import { GroupsCard } from "@/components/settings/GroupsCard";
import { HomeAssistantCard } from "@/components/settings/HomeAssistantCard";
import { OidcProvidersCard } from "@/components/settings/OidcProvidersCard";
import { PrinterEnabledCard } from "@/components/settings/PrinterEnabledCard";
import { PrinterSetupCard } from "@/components/settings/PrinterSetupCard";
import { PrintablesAccountCard } from "@/components/settings/PrintablesAccountCard";
import { PrintCostCard } from "@/components/settings/PrintCostCard";
import { ScanReport } from "@/components/settings/ScanReport";
import { SiteTokensCard } from "@/components/settings/SiteTokensCard";
import { SlicerIntegrationCard } from "@/components/settings/SlicerIntegrationCard";
import { SpoolmanCard } from "@/components/settings/SpoolmanCard";
import { StorageBackendsCard } from "@/components/settings/StorageBackendsCard";
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
  const canSettings = auth.can("settings:write");
  const isAdmin = auth.can("admin");

  return (
    <PageContainer width="default">
      <div>
        <h1 className="text-lg font-semibold text-foreground">Settings</h1>
        <p className="text-sm text-muted-foreground">
          Automation, storage backends, printer, and connected accounts.
        </p>
      </div>

      {backendsQuery.isLoading ? (
        <Skeleton className="h-72 w-full rounded-xl" />
      ) : backendsQuery.isError ? (
        <Card className="mx-auto mt-12 max-w-md">
          <CardHeader className="items-center text-center">
            <CardTitle>Couldn&apos;t load settings</CardTitle>
            <CardDescription>
              {backendsQuery.error instanceof ApiError ? backendsQuery.error.detail : "Something went wrong."}
            </CardDescription>
          </CardHeader>
          <div className="flex justify-center pb-4">
            <Button type="button" onClick={() => void backendsQuery.refetch()}>
              Retry
            </Button>
          </div>
        </Card>
      ) : (
        <Tabs defaultValue="general">
          <TabsList>
            <TabsTrigger value="general">General</TabsTrigger>
            {canSettings && <TabsTrigger value="storage">Storage</TabsTrigger>}
            {canSettings && <TabsTrigger value="printer">Printer</TabsTrigger>}
            <TabsTrigger value="accounts">Accounts</TabsTrigger>
            {isAdmin && <TabsTrigger value="access">Access</TabsTrigger>}
          </TabsList>
          <TabsContent value="general" className="space-y-6">
            <AutomationCard />
            <CategoriesSection />
            <PrintCostCard />
            {auth.can("inventory:write") && <SpoolmanCard />}
            {auth.data?.has_password && <ChangePasswordCard />}
          </TabsContent>
          {canSettings && (
            <TabsContent value="storage" className="space-y-6">
              <StorageBackendsCard />
              <ScanReport />
            </TabsContent>
          )}
          {canSettings && (
            <TabsContent value="printer" className="space-y-6">
              <PrinterEnabledCard />
              <PrinterSetupCard />
              <HomeAssistantCard />
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
              <UsersCard />
              <GroupsCard />
              <OidcProvidersCard />
            </TabsContent>
          )}
        </Tabs>
      )}
    </PageContainer>
  );
}
