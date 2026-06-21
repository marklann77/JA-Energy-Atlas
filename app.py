# --- ENVIRONMENT SETUP REFERENCE (KEEP FOR REFERENCE) ---

### 1. Create a virtual environment named 'atlas_env' 
# FIX: Changed 'cpython' to the correct standard 'python3' command
# python3 -m venv atlas_env

### 2. Activate it (Mac)
# source atlas_env/bin/activate

### 3. Activate it (Windows)
# .\atlas_env\Scripts\activate

### 4. Install the required core web-mapping packages
# pip install panel geemap geopandas nbconvert jupyter_bokeh pyogrio folium

### 5. Launch the local interactive server tab straight to the browser
# panel serve app.py --show

#CTRL + C in the terminal to stop the server when done

# --- END REFERENCE ---

import os
import warnings
import geopandas as gpd
import panel as pn
import folium

# --- Initialize Panel ---
pn.extension(design='bootstrap')

base_dir = os.path.dirname(os.path.abspath(__file__)) if '__file__' in locals() else os.getcwd()

# --- 1. Load Local Data ---
parish_gdf  = gpd.read_file(os.path.join(base_dir, "assets", "ja_parishes.json"))
poverty_gdf = gpd.read_file(os.path.join(base_dir, "assets", "jamaica_poverty_2012.geojson"))
grid_gdf    = gpd.read_file(os.path.join(base_dir, "assets", "overpass_energy_infra_2.geojson"))

# Stringify all non-geometry columns to prevent JSON serialization errors
for df in [parish_gdf, poverty_gdf, grid_gdf]:
    for col in df.columns:
        if col != 'geometry':
            df[col] = df[col].astype(str)

# Filter to valid geometry types only
valid_types = ['Point', 'MultiPoint', 'LineString', 'MultiLineString', 'Polygon', 'MultiPolygon']
grid_gdf = grid_gdf[grid_gdf.geometry.geom_type.isin(valid_types)].copy()

# Filter infrastructure layers
transmission_gdf = grid_gdf[grid_gdf['power'] == 'line'].copy()

# Convert plant polygons to centroids for marker placement
plants_gdf = grid_gdf[grid_gdf['power'] == 'plant'].copy()
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    plants_gdf['geometry'] = plants_gdf.geometry.centroid

# --- Parish name normalization ---
# poverty_gdf['PARISH'] has inconsistent casing/spacing (e.g. 'ST. THOMAS', 'St.Elizabeth').
# parish_gdf['name'] is treated as the clean source of truth. We normalize both to a
# common key (uppercase, no periods, single spaces) purely for matching — display
# always uses the clean parish_gdf['name'] version.
def normalize_parish(name):
    if not isinstance(name, str):
        return ""
    return " ".join(name.upper().replace(".", "").split())

parish_gdf['_parish_key'] = parish_gdf['name'].apply(normalize_parish)
poverty_gdf['_parish_key'] = poverty_gdf['PARISH'].apply(normalize_parish)

# Source of truth: parish names from ja_parishes.json
parish_list = sorted(parish_gdf['name'].dropna().unique().tolist())

# Lookup: clean parish name -> normalized key, so we can filter poverty_gdf correctly
parish_name_to_key = dict(zip(parish_gdf['name'], parish_gdf['_parish_key']))

# Community list builder: depends on selected parish
def get_community_list(selected_parish):
    if selected_parish == 'Island-Wide':
        df = poverty_gdf
    else:
        key = parish_name_to_key.get(selected_parish)
        df = poverty_gdf[poverty_gdf['_parish_key'] == key]
    if 'COMM_NAME' in df.columns:
        return ['All Communities'] + sorted(df['COMM_NAME'].dropna().unique().tolist())
    return ['All Communities']

# --- 2. Widgets ---
parish_selector = pn.widgets.Select(
    name='Select Parish',
    options=['Island-Wide'] + parish_list,
    width=270
)

community_selector = pn.widgets.Select(
    name='Select Community',
    options=['All Communities'],
    width=270
)

# Update community dropdown when parish changes
def update_communities(event):
    community_selector.options = get_community_list(event.new)
    community_selector.value = 'All Communities'

parish_selector.param.watch(update_communities, 'value')

# --- 3. Map Builder ---
def build_map(selected_parish, selected_community):
    m = folium.Map(location=[18.15, -77.3], zoom_start=9, tiles="CartoDB dark_matter")

    # Nighttime lights — commented out, tile source unreliable
    # folium.TileLayer(
    #     tiles='https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/VIIRS_Black_Marble_NightLights_3Band_2016/default/GoogleMapsCompatible_Level8/{z}/{y}/{x}.jpg',
    #     attr='NASA EOSDIS GIBS',
    #     name='Nighttime Lights',
    #     overlay=True,
    #     opacity=0.6
    # ).add_to(m)

    # Parish Boundaries — gray, source of truth from ja_parishes.json
    folium.GeoJson(
        parish_gdf,
        name="Parish Boundaries",
        style_function=lambda x: {'color': '#A0A0A0', 'fillOpacity': 0, 'weight': 1.5},
        tooltip=folium.GeoJsonTooltip(
            fields=['name'],
            aliases=['Parish:'],
            style="background-color:#0f2535; color:#ffffff; font-family:monospace;"
        )
    ).add_to(m)

    # Communities — filtered by parish then community
    if selected_parish == 'Island-Wide':
        communities_to_show = poverty_gdf
    else:
        key = parish_name_to_key.get(selected_parish)
        communities_to_show = poverty_gdf[poverty_gdf['_parish_key'] == key]

    if selected_community != 'All Communities' and 'COMM_NAME' in communities_to_show.columns:
        communities_to_show = communities_to_show[
            communities_to_show['COMM_NAME'] == selected_community
        ]

    tooltip_fields  = [c for c in ['COMM_NAME', 'PARISH', 'av_CONS'] if c in poverty_gdf.columns]
    tooltip_aliases = ['Community:', 'Parish:', 'Avg Consumption:'][:len(tooltip_fields)]

    folium.GeoJson(
        communities_to_show,
        name="Communities",
        style_function=lambda x: {'color': '#FFFFFF', 'fillOpacity': 0.05, 'weight': 0.5, 'opacity': 0.4},
        tooltip=folium.GeoJsonTooltip(
            fields=tooltip_fields,
            aliases=tooltip_aliases,
            style="background-color:#0f2535; color:#F5A623; font-family:monospace;"
        )
    ).add_to(m)

    # Transmission Lines — gold
    folium.GeoJson(
        transmission_gdf,
        name="Transmission Lines",
        style_function=lambda x: {'color': '#F5A623', 'weight': 2.0}
    ).add_to(m)

    # Power Plants
    for _, row in plants_gdf.iterrows():
        plant_name = row.get('name', 'Unnamed Plant')
        source     = row.get('plant:source', 'Unknown')
        capacity   = row.get('plant:output:electricity', 'Unknown')
        popup_html = f"<b style='color:#FF3D00'>{plant_name}</b><br>Fuel: {source}<br>Capacity: {capacity}"
        folium.Marker(
            location=[row.geometry.y, row.geometry.x],
            popup=folium.Popup(popup_html, max_width=250),
            icon=folium.Icon(color='red', icon='bolt', prefix='fa'),
        ).add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)
    return m

# --- 4. Reactive Map ---
@pn.depends(parish_selector.param.value, community_selector.param.value)
def map_view(selected_parish, selected_community):
    m = build_map(selected_parish, selected_community)
    return pn.pane.plot.Folium(m, min_height=500, sizing_mode='stretch_both')

# --- 5. Reactive Parish Info ---
@pn.depends(parish_selector.param.value, community_selector.param.value)
def parish_info(selected_parish, selected_community):
    if selected_parish == 'Island-Wide':
        text = "Viewing all 14 parishes. Select a parish to filter communities."
    else:
        key = parish_name_to_key.get(selected_parish)
        subset = poverty_gdf[poverty_gdf['_parish_key'] == key]
        n = len(subset)
        if selected_community != 'All Communities':
            text = f"**{selected_parish} → {selected_community}**\n\nZoomed to selected community."
        else:
            text = f"**{selected_parish}**\n\n{n} communities mapped."
    return pn.pane.Markdown(text, styles={"color": "#8fa8b8", "font-size": "13px"})

# --- 6. Metric Cards ---
def metric_card(label, value, color="#F5A623"):
    return pn.pane.HTML(f"""
        <div style="background:#0f2535; border-left:4px solid {color}; border-radius:6px;
                    padding:12px 16px; margin-bottom:10px; font-family:monospace;">
            <div style="color:#8fa8b8; font-size:11px; text-transform:uppercase; letter-spacing:1px;">{label}</div>
            <div style="color:{color}; font-size:24px; font-weight:700; margin-top:4px;">{value}</div>
        </div>""", width=280)

# --- 7. Bottom Metrics Bar (~2.5x taller) ---
bottom_bar = pn.Row(
    pn.pane.HTML("""
        <div style="background:#0f2535; padding:32px 32px; border-top:2px solid #F5A623;
                    font-family:monospace; width:100%; box-sizing:border-box;">
            <div style="color:#F5A623; font-size:11px; text-transform:uppercase;
                        letter-spacing:2px; margin-bottom:20px;">Parish Snapshot</div>
            <div style="display:flex; gap:40px; flex-wrap:wrap; margin-bottom:28px;">
                <div>
                    <div style="color:#8fa8b8; font-size:10px; margin-bottom:4px;">Total Parishes</div>
                    <div style="color:#fff; font-size:28px; font-weight:700;">14</div>
                </div>
                <div>
                    <div style="color:#8fa8b8; font-size:10px; margin-bottom:4px;">Population (2021)</div>
                    <div style="color:#fff; font-size:28px; font-weight:700;">2.73M</div>
                </div>
                <div>
                    <div style="color:#8fa8b8; font-size:10px; margin-bottom:4px;">Grid Connected</div>
                    <div style="color:#fff; font-size:28px; font-weight:700;">~96%</div>
                </div>
                <div>
                    <div style="color:#8fa8b8; font-size:10px; margin-bottom:4px;">Renewables Share</div>
                    <div style="color:#fff; font-size:28px; font-weight:700;">~15%</div>
                </div>
                <div>
                    <div style="color:#8fa8b8; font-size:10px; margin-bottom:4px;">Vision 2030 Target</div>
                    <div style="color:#F5A623; font-size:28px; font-weight:700;">50%</div>
                </div>
                <div>
                    <div style="color:#8fa8b8; font-size:10px; margin-bottom:4px;">Avg Energy Burden</div>
                    <div style="color:#fff; font-size:28px; font-weight:700;">~10%</div>
                </div>
            </div>
            <div style="color:#F5A623; font-size:11px; text-transform:uppercase;
                        letter-spacing:2px; margin-bottom:16px;">Vision 2030 Decarbonization Progress</div>
            <div style="display:flex; gap:40px; flex-wrap:wrap;">
                <div>
                    <div style="color:#8fa8b8; font-size:10px; margin-bottom:4px;">Solar Capacity (MW)</div>
                    <div style="color:#fff; font-size:28px; font-weight:700;">~150</div>
                    <div style="color:#8fa8b8; font-size:9px;">Target: 1,000 MW by 2030</div>
                </div>
                <div>
                    <div style="color:#8fa8b8; font-size:10px; margin-bottom:4px;">Wind Capacity (MW)</div>
                    <div style="color:#fff; font-size:28px; font-weight:700;">~70</div>
                    <div style="color:#8fa8b8; font-size:9px;">Target: 500 MW by 2030</div>
                </div>
                <div>
                    <div style="color:#8fa8b8; font-size:10px; margin-bottom:4px;">Distribution Losses</div>
                    <div style="color:#fff; font-size:28px; font-weight:700;">~26%</div>
                    <div style="color:#8fa8b8; font-size:9px;">Caribbean avg: ~15%</div>
                </div>
                <div>
                    <div style="color:#8fa8b8; font-size:10px; margin-bottom:4px;">Avg Electricity Rate</div>
                    <div style="color:#fff; font-size:28px; font-weight:700;">~$0.40/kWh</div>
                    <div style="color:#8fa8b8; font-size:9px;">Among highest in region</div>
                </div>
                <div>
                    <div style="color:#8fa8b8; font-size:10px; margin-bottom:4px;">High Burden Parishes</div>
                    <div style="color:#FF3D00; font-size:28px; font-weight:700;">6 / 14</div>
                    <div style="color:#8fa8b8; font-size:9px;">Burden > 10% of expenditure</div>
                </div>
            </div>
        </div>
    """, sizing_mode='stretch_width'),
    sizing_mode='stretch_width',
    margin=0
)

# --- 8. Legend ---
legend_html = """
<div style="background:#0f2535; padding:15px; border-radius:6px; font-family:monospace; color:#8fa8b8;">
    <div style="color:#F5A623; font-size:12px; text-transform:uppercase; letter-spacing:1px; margin-bottom:12px;">Legend</div>
    <div style="display:flex; align-items:center; margin-bottom:8px;">
        <div style="width:14px; height:14px; background:#FF3D00; border-radius:50%; margin-right:10px;"></div> Power Plants
    </div>
    <div style="display:flex; align-items:center; margin-bottom:8px;">
        <div style="width:20px; height:3px; background:#F5A623; margin-right:10px;"></div> Transmission Lines
    </div>
    <div style="display:flex; align-items:center; margin-bottom:8px;">
        <div style="width:20px; height:3px; background:#A0A0A0; margin-right:10px;"></div> Parish Boundaries
    </div>
    <div style="display:flex; align-items:center; margin-bottom:8px;">
        <div style="width:14px; height:14px; background:#ffffff22; border:1px solid #fff; margin-right:10px;"></div> Communities
    </div>
</div>"""

# --- 9. Sidebar ---
sidebar_content = pn.Column(
    pn.pane.Markdown("## BSC Metrics", styles={"color": "#F5A623"}),
    metric_card("Overall Resiliency", "—"),
    metric_card("Energy Justice",     "—"),
    metric_card("Committed",          "—"),
    metric_card("Carbon Intensity",   "—"),
    pn.layout.Divider(),
    parish_selector,
    community_selector,
    parish_info,
    pn.layout.Divider(),
    pn.pane.HTML(legend_html, width=280),
    width=300,
)

# --- 10. Main Layout ---
main_content = pn.Column(
    pn.pane.Markdown("# Map Overview", styles={"color": "#F5A623"}),
    map_view,
    bottom_bar,
    sizing_mode='stretch_both',
    margin=10
)

# --- 11. Template ---
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

# Testing

consumption_gdf = gpd.read_file(
    os.path.join(base_dir, "assets", "jamaica_consumption_2012.geojson")
)

import pandas as pd
dwel_df = pd.read_csv(
    os.path.join(base_dir, "assets", "PopulationandDwellingCountsbyCommunity_.csv")
)

print("\nCONSUMPTION COLUMNS")
print(consumption_gdf.columns.tolist())

print("\nCONSUMPTION HEAD")
print(
    consumption_gdf[
        ['COMM_NAME','PARISH','POP']
    ].head()
)

print("\nDWELLING COLUMNS")
print(dwel_df.columns.tolist())

print("\nDWELLING HEAD")
print(dwel_df.head())
