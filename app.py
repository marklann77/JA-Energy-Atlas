# --- ENVIRONMENT SETUP REFERENCE (KEEP FOR REFERENCE) ---

### 1. Create a virtual environment named 'atlas_env' 
# FIX: Changed 'cpython' to the correct standard 'python3' command
# python3 -m venv atlas_env

### 2. Activate it (Mac)
# source atlas_env/bin/activate

### 3. Activate it (Windows)
# .\atlas_env\Scripts\activate

### 4. Install the required core web-mapping packages
# pip install panel geemap geopandas nbconvert jupyter_bokeh pyogrio

### 5. Launch the local interactive server tab straight to the browser
# panel serve app.py --show

#CTRL + C in the terminal to stop the server when done

# --- END REFERENCE ---

import os
import ee
import geemap
import geopandas as gpd
import panel as pn
 
# --- Initialize ---
ee.Initialize()
pn.extension('ipywidgets', design='bootstrap')
 
# --- Load Parish GeoJSON ---
base_dir = os.path.dirname(os.path.abspath(__file__)) if '__file__' in locals() else os.getcwd()
geojson_path = os.path.join(base_dir, "assets", "ja_parishes.json")
gdf = gpd.read_file(geojson_path)

import json
from shapely.geometry import shape, mapping, LineString, Point, MultiLineString
from shapely.ops import unary_union

# --- Load Overpass Turbo Energy Infrastructure JSON and convert to GeoDataFrame ---
def overpass_to_gdf(path):
    with open(path) as f:
        data = json.load(f)

    # Build a node id -> (lon, lat) lookup for way geometry reconstruction
    node_coords = {
        el['id']: (el['lon'], el['lat'])
        for el in data['elements']
        if el['type'] == 'node' and 'lon' in el
    }

    rows = []
    for el in data['elements']:
        tags = el.get('tags', {})
        if not tags.get('power'):
            continue

        if el['type'] == 'node' and 'lon' in el:
            geom = Point(el['lon'], el['lat'])
        elif el['type'] == 'way':
            coords = [node_coords[n] for n in el.get('nodes', []) if n in node_coords]
            if len(coords) < 2:
                continue
            geom = LineString(coords)
        else:
            continue

        rows.append({
            'geometry': geom,
            'power':    tags.get('power', ''),
            'name':     tags.get('name', ''),
            'voltage':  tags.get('voltage', ''),
            'operator': tags.get('operator', ''),
            'output_mw': tags.get('plant:output:electricity', ''),
            'source':   tags.get('plant:source', ''),
        })

    return gpd.GeoDataFrame(rows, crs="EPSG:4326")

power_gdf = overpass_to_gdf(os.path.join(base_dir, "assets", "overpass_energy_infra.json"))

# --- Convert to EE and style by power type ---
power_ee = geemap.geopandas_to_ee(power_gdf)

def style_layer(power_type, color, width=1.5):
    subset = power_ee.filter(ee.Filter.eq('power', power_type))
    return subset.style(color=color, fillColor=color + "55", width=width)

# (power layers added below after m is initialized)
 
# --- Placeholder STATIN Data ---
statin_data = {
    "Kingston":      {"expenditure": 650000, "energy_burden": 0.06},
    "St. Andrew":    {"expenditure": 720000, "energy_burden": 0.05},
    "St. Catherine": {"expenditure": 580000, "energy_burden": 0.08},
    "Clarendon":     {"expenditure": 490000, "energy_burden": 0.11},
    "Manchester":    {"expenditure": 520000, "energy_burden": 0.09},
    "St. Elizabeth": {"expenditure": 460000, "energy_burden": 0.13},
    "Westmoreland":  {"expenditure": 480000, "energy_burden": 0.12},
    "Hanover":       {"expenditure": 500000, "energy_burden": 0.10},
    "St. James":     {"expenditure": 610000, "energy_burden": 0.07},
    "Trelawny":      {"expenditure": 470000, "energy_burden": 0.11},
    "St. Ann":       {"expenditure": 530000, "energy_burden": 0.09},
    "St. Mary":      {"expenditure": 440000, "energy_burden": 0.14},
    "Portland":      {"expenditure": 430000, "energy_burden": 0.15},
    "St. Thomas":    {"expenditure": 410000, "energy_burden": 0.16},
}
 
gdf['expenditure']   = gdf['name'].map(lambda x: statin_data.get(x, {}).get('expenditure', 0))
gdf['energy_burden'] = gdf['name'].map(lambda x: statin_data.get(x, {}).get('energy_burden', 0.0))
 
# ---  Convert GDF to EE FeatureCollection and style it ---
jamaica_parishes = geemap.geopandas_to_ee(gdf)
 
parish_style = {
    "color":     "F5A623",   # gold outline
    "fillColor": "1a3a4a",   # dark teal fill
    "width":     1.5
}
styled = jamaica_parishes.style(**parish_style)
 
# ---  Build geemap Map (this is a real ipywidget) ---
m = geemap.Map(center=(18.1, -77.3), zoom=9)
m.layout.height = "700px"
m.layout.width  = "100%"
m.add_basemap("CartoDB.DarkMatter")
m.addLayer(styled, {}, "Jamaica Parishes")

# --- Power Infrastructure Layers (order matters: lines first, points on top) ---
m.addLayer(style_layer('line',       'FF4444', width=2), {}, "Transmission Lines")
m.addLayer(style_layer('minor_line', 'FF8C00', width=1), {}, "Distribution Lines")
m.addLayer(style_layer('substation', 'FFD700', width=1), {}, "Substations")
m.addLayer(style_layer('plant',      '00FF88', width=2), {}, "Power Plants")
 
# --- Sidebar Metric Cards (plain Panel components, no fake library) ---
def metric_card(label, value, color="#F5A623"):
    return pn.pane.HTML(
        f"""
        <div style="
            background: #0f2535;
            border-left: 4px solid {color};
            border-radius: 6px;
            padding: 12px 16px;
            margin-bottom: 10px;
            font-family: monospace;
        ">
            <div style="color:#8fa8b8; font-size:11px; text-transform:uppercase; letter-spacing:1px;">{label}</div>
            <div style="color:#F5A623; font-size:24px; font-weight:700; margin-top:4px;">{value}</div>
        </div>
        """,
        width=280
    )
 
sidebar_content = pn.Column(
    pn.pane.Markdown("## BSC Metrics", styles={"color": "#F5A623"}),
    metric_card("Overall Resiliency",    "42%"),
    metric_card("Energy Justice",        "25%"),
    metric_card("Committed",             "10%"),
    metric_card("Carbon Intensity",      "35%"),
    pn.layout.Divider(),
    pn.pane.Markdown("### About", styles={"color": "#8fa8b8"}),
    pn.pane.Markdown(
        "Parish-level decision-support tool for equitable decarbonization "
        "aligned with Jamaica Vision 2030.",
        styles={"color": "#8fa8b8", "font-size": "12px"}
    ),
    width=300,
)
 
# ---  Main Content ---
map_pane = pn.pane.IPyWidget(m, sizing_mode='stretch_both', min_height=700)
 
main_content = pn.Column(
    pn.pane.Markdown("# Parish Workspace View"),
    map_pane,
    sizing_mode='stretch_both',
    margin=10
)
 
# --- Assemble Template ---
template = pn.template.FastListTemplate(
    title="Jamaica Energy Atlas",
    sidebar_width=320,
    accent_base_color="#2F4F4F",
    header_background="#1a3a4a",
    sidebar=[sidebar_content],
    main=[main_content],
)
 
template.servable()
 
