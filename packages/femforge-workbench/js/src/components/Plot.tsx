import Plotly from "plotly.js/lib/core";
import mesh3d from "plotly.js/lib/mesh3d";
import parcoords from "plotly.js/lib/parcoords";
import scatter3d from "plotly.js/lib/scatter3d";
import surface from "plotly.js/lib/surface";
import createPlotlyComponent from "react-plotly.js/factory";
import type { Config, Data, Datum, Layout, PlotMouseEvent, PlotRelayoutEvent, PlotRestyleEvent } from "plotly.js";

// The dist bundle carries every trace plotly has, including the map stack; these are the ones the shell and the lab draw.
Plotly.register([mesh3d, parcoords, scatter3d, surface]);

const PlotlyComponent = createPlotlyComponent(Plotly);

export interface Figure {
  data?: Data[];
  layout?: Partial<Layout>;
  config?: Partial<Config>;
}

export interface ClickPoint {
  x: Datum;
  y: Datum;
  curveNumber: number;
  pointNumber: number;
}

export interface ClickEvent {
  points: ClickPoint[];
}

export interface PlotProps {
  figure?: Figure;
  onRestyle?: (event: PlotRestyleEvent) => void;
  onRelayout?: (event: PlotRelayoutEvent) => void;
  onClick?: (event: ClickEvent) => void;
  className?: string;
}

export function Plot({ figure = {}, onRestyle, onRelayout, onClick, className }: PlotProps) {
  const { data = [], layout = {}, config = {} } = figure;

  function handleClick(event: PlotMouseEvent): void {
    const points = event.points.map((point) => ({
      x: point.x,
      y: point.y,
      curveNumber: point.curveNumber,
      pointNumber: point.pointNumber,
    }));
    onClick?.({ points });
  }

  return (
    <PlotlyComponent
      className={className}
      data={data}
      layout={{ autosize: true, ...layout }}
      config={{ responsive: true, displaylogo: false, ...config }}
      style={{ width: "100%", height: "100%" }}
      useResizeHandler
      onRestyle={onRestyle}
      onRelayout={onRelayout}
      onClick={handleClick}
    />
  );
}
