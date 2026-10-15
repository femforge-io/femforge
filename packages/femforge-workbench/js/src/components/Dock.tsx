import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  type ReactNode,
  type Ref,
} from "react";
import {
  DockviewReact,
  type AddPanelPositionOptions,
  type DockviewApi,
  type DockviewPanelRenderer,
  type DockviewReadyEvent,
  type DockviewTheme,
  type IDockviewHeaderActionsProps,
  type IDockviewPanelProps,
  type SerializedDockview,
} from "dockview-react";
import { AppWindow, ExternalLink, PictureInPicture2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

const CONTENT_COMPONENT = "DockPanel";
const LAYOUT_DEBOUNCE_MS = 100;
const DEFAULT_POPOUT_URL = "/__ff/popout.html";

export interface DockPanelParams {
  templateName: string;
}

export type RenderPanel = (params: DockPanelParams) => ReactNode;
export type FloatingGroupBounds =
  "boundedWithinViewport" | { minimumHeightWithinViewport?: number; minimumWidthWithinViewport?: number };

/** The add-panel options the Python placement sends beside id, title and template. */
export interface DockAddOn {
  position?: AddPanelPositionOptions;
  inactive?: boolean;
  initialWidth?: number;
  initialHeight?: number;
  renderer?: DockviewPanelRenderer;
}

export interface VisibilityEvent {
  id: string;
  visible: boolean;
}

export interface PanelEvent {
  id: string;
}

export interface ActivePanelEvent {
  id: string | null;
}

export interface DockHandle {
  addPanel(id: string, title: string, templateName: string, addOn?: DockAddOn): void;
  removePanel(id: string): void;
  activePanel(id: string): void;
  activePanelId(): string | null;
  setTitle(id: string, title: string): void;
  maximize(id: string): void;
  restoreMaximized(): void;
  restoreLayout(layout: SerializedDockview): void;
  toJSON(): SerializedDockview | undefined;
  moveTo(id: string, targetId: string): void;
  panelIds(): string[];
  api(): DockviewApi | null;
}

export interface DockProps {
  ref?: Ref<DockHandle>;
  renderPanel?: RenderPanel;
  onReady?: () => void;
  onLayoutChange?: (layout: SerializedDockview) => void;
  onVisibility?: (event: VisibilityEvent) => void;
  onActivePanel?: (event: ActivePanelEvent) => void;
  onPanelRemoved?: (event: PanelEvent) => void;
  dark?: boolean;
  popoutUrl?: string;
  floatingGroupBounds?: FloatingGroupBounds;
  className?: string;
}

interface DockContextValue {
  renderPanel: RenderPanel | null;
  onVisibility: ((event: VisibilityEvent) => void) | null;
}

const DockContext = createContext<DockContextValue>({ renderPanel: null, onVisibility: null });

// The panel body: the server template named by the panel params, rendered through the slot the Python side passed.
function DockPanel({ api, params }: IDockviewPanelProps<DockPanelParams>) {
  const { renderPanel, onVisibility } = useContext(DockContext);
  const mountedAt = useRef(Date.now()).current;

  useEffect(() => {
    if (!onVisibility) return undefined;

    // dockview only reports a change, so a panel that mounts visible would never announce itself to the server.
    onVisibility({ id: api.id, visible: api.isVisible });

    const disposable = api.onDidVisibilityChange((event) => onVisibility({ id: api.id, visible: event.isVisible }));
    return () => disposable.dispose();
  }, [api, onVisibility]);

  return (
    <div className="h-full w-full overflow-hidden" data-mounted={mountedAt} data-panel={api.id}>
      {renderPanel ? renderPanel(params) : null}
    </div>
  );
}

const components = { [CONTENT_COMPONENT]: DockPanel };

interface HeaderActionProps {
  label: string;
  children: ReactNode;
  onClick?: () => void;
  href?: string;
}

function HeaderAction({ label, children, onClick, href }: HeaderActionProps) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        {href ? (
          <Button asChild variant="ghost" size="icon-xs">
            <a href={href} target="_blank" rel="noreferrer" aria-label={label}>
              {children}
            </a>
          </Button>
        ) : (
          <Button variant="ghost" size="icon-xs" aria-label={label} onClick={onClick}>
            {children}
          </Button>
        )}
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  );
}

function HeaderActions({ containerApi, group, activePanel }: IDockviewHeaderActionsProps) {
  const params = activePanel?.params as DockPanelParams | undefined;
  const templateName = params?.templateName;

  return (
    <div className="flex h-full items-center gap-0.5 px-1 text-muted-foreground">
      <HeaderAction label="Pop out" onClick={() => void containerApi.addPopoutGroup(group)}>
        <ExternalLink />
      </HeaderAction>
      <HeaderAction label="Float" onClick={() => containerApi.addFloatingGroup(group)}>
        <PictureInPicture2 />
      </HeaderAction>
      {templateName ? (
        <HeaderAction label="Open in a tab" href={`/?ui=${templateName}`}>
          <AppWindow />
        </HeaderAction>
      ) : null}
    </div>
  );
}

function themeFor(dark: boolean): DockviewTheme {
  return { name: "ff", className: "dockview-theme-ff", colorScheme: dark ? "dark" : "light" };
}

export function Dock({
  ref,
  renderPanel,
  onReady,
  onLayoutChange,
  onVisibility,
  onActivePanel,
  onPanelRemoved,
  dark = true,
  popoutUrl = DEFAULT_POPOUT_URL,
  floatingGroupBounds = "boundedWithinViewport",
  className,
}: DockProps) {
  const apiRef = useRef<DockviewApi | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  // Dockview fires onDidRemovePanel for its own teardown too (fromJSON clears the dock), and synchronously, so a counter around our own calls tells a user close apart.
  const teardown = useRef(0);
  const context = useMemo<DockContextValue>(
    () => ({ renderPanel: renderPanel ?? null, onVisibility: onVisibility ?? null }),
    [renderPanel, onVisibility],
  );
  const theme = useMemo(() => themeFor(dark), [dark]);

  const handleReady = useCallback(
    (event: DockviewReadyEvent) => {
      const api = event.api;
      apiRef.current = api;
      api.onDidLayoutChange(() => {
        clearTimeout(timer.current);
        timer.current = setTimeout(() => onLayoutChange?.(api.toJSON()), LAYOUT_DEBOUNCE_MS);
      });
      api.onDidActivePanelChange((change) => onActivePanel?.({ id: change.panel?.id ?? null }));
      api.onDidRemovePanel((panel) => {
        if (teardown.current === 0) onPanelRemoved?.({ id: panel.id });
      });
      onReady?.();
    },
    [onLayoutChange, onReady, onActivePanel, onPanelRemoved],
  );

  useImperativeHandle(ref, () => ({
    addPanel: (id, title, templateName, addOn = {}) => {
      const api = apiRef.current;
      if (!api || api.getPanel(id)) return;

      api.addPanel<DockPanelParams>({ ...addOn, id, title, component: CONTENT_COMPONENT, params: { templateName } });
    },
    removePanel: (id) => {
      const panel = apiRef.current?.getPanel(id);
      if (!panel) return;

      teardown.current += 1;
      try {
        apiRef.current?.removePanel(panel);
      } finally {
        teardown.current -= 1;
      }
    },
    activePanel: (id) => apiRef.current?.getPanel(id)?.api.setActive(),
    activePanelId: () => apiRef.current?.activePanel?.id ?? null,
    setTitle: (id, title) => apiRef.current?.getPanel(id)?.api.setTitle(title),
    maximize: (id) => apiRef.current?.getPanel(id)?.api.maximize(),
    restoreMaximized: () => apiRef.current?.exitMaximizedGroup(),
    restoreLayout: (layout) => {
      teardown.current += 1;
      try {
        apiRef.current?.fromJSON(layout);
      } catch (error) {
        console.error("ff-dock restoreLayout", error);
      } finally {
        teardown.current -= 1;
      }
    },
    toJSON: () => apiRef.current?.toJSON(),
    api: () => apiRef.current,
    moveTo: (id, targetId) => {
      const api = apiRef.current;
      const panel = api?.getPanel(id);
      const target = api?.getPanel(targetId);
      if (panel && target) panel.api.moveTo({ group: target.group });
    },
    panelIds: () => apiRef.current?.panels.map((panel) => panel.id) ?? [],
  }));

  return (
    <TooltipProvider delayDuration={300}>
      <DockContext.Provider value={context}>
        <DockviewReact
          className={cn("h-full w-full", className)}
          components={components}
          rightHeaderActionsComponent={HeaderActions}
          onReady={handleReady}
          theme={theme}
          popoutUrl={popoutUrl}
          floatingGroupBounds={floatingGroupBounds}
          defaultRenderer="always"
        />
      </DockContext.Provider>
    </TooltipProvider>
  );
}
