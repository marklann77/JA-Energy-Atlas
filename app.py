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
import geopandas as gpd
import panel as pn
import folium

# --- Initialize Panel ---
pn.extension(design='bootstrap')

base_dir = os.path.dirname(os.path.abspath(__file__)) if '__file__' in locals() else os.getcwd()

# --- 1. Load Local Data ---
parish_path = os.path.join(base_dir, "assets", "ja_parishes.json")
parish_gdf = gpd.read_file(parish_path)

poverty_path = os.path.join(base_dir, "assets", "jamaica_poverty_2012.geojson")
poverty_gdf = gpd.read_file(poverty_path)

grid_path = os.path.join(base_dir, "assets", "overpass_energy_infra_2.geojson")
grid_gdf = gpd.read_file(grid_path)

# Convert all metadata to strings to prevent JSON errors
for df in [parish_gdf, poverty_gdf, grid_gdf]:
    for col in df.columns:
        if col != 'geometry':
            df[col] = df[col].astype(str)

valid_types = ['Point', 'MultiPoint', 'LineString', 'MultiLineString', 'Polygon', 'MultiPolygon']
grid_gdf = grid_gdf[grid_gdf.geometry.type.isin(valid_types)].copy()

# Filter Infrastructure
transmission_gdf = grid_gdf[grid_gdf['power'] == 'line']

# GEOMETRY UPGRADE: Convert complex plant polygons into single center-points (centroids) for pins
plants_gdf = grid_gdf[grid_gdf['power'] == 'plant'].copy()
# Suppress the CRS warning since we just need rough visual centers
import warnings
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    plants_gdf['geometry'] = plants_gdf.geometry.centroid

# --- 2. Build Native Folium Map ---
m = folium.Map(location=[18.15, -77.3], zoom_start=9, tiles="CartoDB dark_matter")

# ADD NIGHTTIME LIGHTS (NASA VIIRS Black Marble overlay)
folium.TileLayer(
    tiles='https://map1.vis.earthdata.nasa.gov/wmts-webmerc/VIIRS_CityLights_2012/default//GoogleMapsCompatible_Level8/{z}/{y}/{x}.jpg',
    attr='NASA EarthData',
    name='Nighttime Lights (2012)',
    overlay=True,
    opacity=0.5
).add_to(m)

# Add Communities with HOVER TOOLTIPS
# Using the columns 'COMM_NAME' and 'av_CONS' based on your previous terminal outputs
community_tooltip = folium.GeoJsonTooltip(
    fields=['COMM_NAME', 'PARISH', 'av_CONS'],
    aliases=['Community:', 'Parish:', 'Avg Consumption:'],
    style="background-color: #0f2535; color: #F5A623; font-family: monospace;"
)

folium.GeoJson(
    poverty_gdf,
    name="Communities (Hover for Data)",
    style_function=lambda x: {'color': '#FFFFFF', 'fillOpacity': 0.05, 'weight': 0.5, 'opacity': 0.3},
    tooltip=community_tooltip
).add_to(m)

# Add Parishes
folium.GeoJson(
    parish_gdf,
    name="Parish Boundaries",
    style_function=lambda x: {'color': "#D5D5D5", 'fillOpacity': 0, 'weight': 1.0}
).add_to(m)

# Add Transmission Lines
folium.GeoJson(
    transmission_gdf,
    name="Transmission Lines",
    style_function=lambda x: {'color': "#F8D705", 'weight': 2.0}
).add_to(m)

# Add Power Plants as CLICKABLE MARKERS
# We loop through the plants and drop a specific pin for each one
for idx, row in plants_gdf.iterrows():
    plant_name = row.get('name', 'Unnamed Plant')
    source = row.get('plant:source', 'Unknown Fuel')
    capacity = row.get('plant:output:electricity', 'Unknown Capacity')
    
    # Build a clean HTML popup window
    popup_html = f"<b>{plant_name}</b><br>Source: {source}<br>Capacity: {capacity}"
    
    folium.Marker(
        location=[row.geometry.y, row.geometry.x],
        popup=folium.Popup(popup_html, max_width=250),
        icon=folium.Icon(color='red', icon='bolt', prefix='fa'),
    ).add_to(m)

folium.LayerControl(collapsed=False).add_to(m)

# --- 3. Sidebar UI Elements & Interactivity ---

# 3A. The Parish Dropdown Widget
# Extract a clean, sorted list of unique parish names from the data
parish_list = sorted(poverty_gdf['PARISH'].dropna().unique().tolist())
parish_selector = pn.widgets.Select(name='Select a Parish Focus', options=['Island-Wide'] + parish_list)

# 3B. The Dynamic Info Box (Listens to the dropdown)
@pn.depends(parish_selector.param.value)
def dynamic_parish_info(selected_parish):
    if selected_parish == 'Island-Wide':
        text = "Viewing total island metrics. Select a specific parish from the dropdown to view localized vulnerability data."
    else:
        text = f"**{selected_parish} Analysis:**\n\n*(Placeholder: You can eventually filter your STATIN data here to show exact totals for {selected_parish}.)*"
    
    return pn.pane.Markdown(text, styles={"color": "#8fa8b8", "font-size": "13px"})

def metric_card(label, value, color="#F5A623"):
    return pn.pane.HTML(
        f"""
        <div style="background: #0f2535; border-left: 4px solid {color}; border-radius: 6px; padding: 12px 16px; margin-bottom: 10px; font-family: monospace;">
            <div style="color:#8fa8b8; font-size:11px; text-transform:uppercase; letter-spacing:1px;">{label}</div>
            <div style="color:#F5A623; font-size:24px; font-weight:700; margin-top:4px;">{value}</div>
        </div>
        """, width=280)

legend_html = """
<div style="background: #0f2535; padding: 15px; border-radius: 6px; font-family: monospace; color: #8fa8b8;">
    <h4 style="color: #F5A623; margin-top: 0px; margin-bottom: 15px;">Map Legend</h4>
    <div style="display: flex; align-items: center; margin-bottom: 8px;">
        <div style="width: 15px; height: 15px; background: #FF3D00; border-radius: 50%; margin-right: 10px;"></div> Power Plants
    </div>
    <div style="display: flex; align-items: center; margin-bottom: 8px;">
        <div style="width: 20px; height: 3px; background: #00E5FF; margin-right: 10px;"></div> Transmission Lines
    </div>
    <div style="display: flex; align-items: center; margin-bottom: 8px;">
        <div style="width: 20px; height: 3px; background: #F5A623; margin-right: 10px;"></div> Parish Boundaries
    </div>
</div>
"""

sidebar_content = pn.Column(
    pn.pane.Markdown("## BSC Metrics", styles={"color": "#F5A623"}),
    metric_card("Overall Resiliency",    "0%"),
    metric_card("Energy Justice",        "0%"),
    metric_card("Committed",             "0%"),
    metric_card("Carbon Intensity",      "0%"),
    pn.layout.Divider(),
    parish_selector,       # Inject the Dropdown
    dynamic_parish_info,   # Inject the dynamically updating text box
    pn.layout.Divider(),
    pn.pane.HTML(legend_html, width=280),
    width=300,
)

# --- 4. Main Content Assembly ---
map_pane = pn.pane.plot.Folium(m, min_height=700, sizing_mode='stretch_both')

main_content = pn.Column(
    pn.pane.Markdown("# Map Overview", styles={"color": "#F5A623"}),
    map_pane,
    sizing_mode='stretch_both',
    margin=10
)

# --- 5. Assemble Template ---
# PANEL DARK MODE ACTIVATED: theme='dark' forces the UI to match your map
template = pn.template.FastListTemplate(
    title="Jamaica Energy Atlas",
    sidebar_width=320,
    theme='dark', 
    accent_base_color="#0f2535",
    header_background="#0f2535",
    sidebar=[sidebar_content],
    main=[main_content],
)

template.servable()
 
