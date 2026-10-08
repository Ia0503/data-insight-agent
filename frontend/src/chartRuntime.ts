import { init, use } from "echarts/core";
import { BarChart, LineChart } from "echarts/charts";
import {
  AriaComponent,
  DataZoomComponent,
  GridComponent,
  TooltipComponent,
} from "echarts/components";
import { SVGRenderer } from "echarts/renderers";

// Named imports keep unused chart families out of the lazy report bundle.
use([
  BarChart,
  LineChart,
  AriaComponent,
  DataZoomComponent,
  GridComponent,
  TooltipComponent,
  SVGRenderer,
]);
export const createChart = (element: HTMLElement) =>
  init(element, undefined, { renderer: "svg" });
