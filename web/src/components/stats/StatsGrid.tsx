import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  rectSortingStrategy,
  sortableKeyboardCoordinates,
  useSortable,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { GripVerticalIcon, Maximize2Icon } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { StatsDashboard } from "@/api/stats";
import { WIDGETS } from "@/components/stats/registry";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import type { WidgetId, WidgetLayout } from "@/lib/statsLayout";

const SPAN = { 1: "lg:col-span-1", 2: "lg:col-span-2", 3: "lg:col-span-3" } as const;

function SortableWidget({
  w,
  data,
  onResize,
}: {
  w: WidgetLayout;
  data: StatsDashboard;
  onResize: (id: WidgetId) => void;
}) {
  const { t } = useTranslation();
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: w.id });
  const Body = WIDGETS[w.id];
  const name = t(`stats.w.${w.id}`);
  return (
    <Card
      ref={setNodeRef}
      data-widget={w.id}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className={cn("sm:col-span-2", SPAN[w.size], w.size === 1 && "sm:col-span-1", isDragging && "z-10 opacity-80 shadow-lg")}
    >
      <CardHeader className="flex-row items-center gap-1">
        <button
          type="button"
          className="cursor-grab touch-none rounded p-0.5 text-muted-foreground hover:bg-muted"
          aria-label={t("stats.drag", { name })}
          {...attributes}
          {...listeners}
        >
          <GripVerticalIcon className="size-4" />
        </button>
        <CardTitle className="flex-1 text-sm">{name}</CardTitle>
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          aria-label={t("stats.size", { name, size: t(`stats.sizes.${w.size}`) })}
          onClick={() => onResize(w.id)}
        >
          <Maximize2Icon className="size-3.5" />
        </Button>
      </CardHeader>
      <CardContent>
        <Body d={data} />
      </CardContent>
    </Card>
  );
}

/** Draggable (pointer + keyboard) widget grid; the size button cycles a
 * widget through 1/2/3 columns. */
export function StatsGrid({
  widgets,
  data,
  onReorder,
  onResize,
}: {
  widgets: WidgetLayout[];
  data: StatsDashboard;
  onReorder: (activeId: string, overId: string) => void;
  onResize: (id: WidgetId) => void;
}) {
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );
  function onDragEnd(e: DragEndEvent) {
    if (e.over && e.active.id !== e.over.id) onReorder(String(e.active.id), String(e.over.id));
  }
  return (
    <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
      <SortableContext items={widgets.map((w) => w.id)} strategy={rectSortingStrategy}>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {widgets.map((w) => (
            <SortableWidget key={w.id} w={w} data={data} onResize={onResize} />
          ))}
        </div>
      </SortableContext>
    </DndContext>
  );
}
