# LAST UPDATED: 6-25-2026

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
import pandas as pd
import panel as pn
import folium

# --- Initialize Panel ---
pn.extension(design='bootstrap')

base_dir = os.path.dirname(os.path.abspath(__file__)) if '__file__' in locals() else os.getcwd()

# --- JPS Blended Residential Rate (late 2025 / early 2026) ---
# Source: blended effective rate including fuel/IPP charges, fixed fees, taxes.
# av_CONS in our consumption file is in JMD already (confirmed by magnitude check),
# so this rate is NOT applied to av_CONS directly — kept here for reference/labeling only.
JPS_BLENDED_RATE_JMD_PER_KWH = 46.00

# --- 1. Load Local Data ---
parish_gdf      = gpd.read_file(os.path.join(base_dir, "assets", "ja_parishes.json")) # Load parish boundaries
poverty_gdf     = gpd.read_file(os.path.join(base_dir, "assets", "jamaica_poverty_2012.geojson")) # Load poverty data
consumption_gdf = gpd.read_file(os.path.join(base_dir, "assets", "jamaica_consumption_2012.geojson")) # Load consumption data
grid_gdf        = gpd.read_file(os.path.join(base_dir, "assets", "overpass_energy_infra_2.geojson")) # Load grid data
dwel_df         = pd.read_csv(os.path.join(base_dir, "assets", "PopulationandDwellingCountsbyCommunity_.csv")) # Load dwelling data

# --- CRS FIX ---
# parish_gdf is correctly in EPSG:4326 (real lat/lon, e.g. -77, 18).
# poverty_gdf and consumption_gdf report .crs as EPSG:4326 but their actual coordinate
# VALUES are in Web Mercator meters (e.g. -8700000, 2050000) — the CRS tag is wrong/stale.
# We force-assign the correct original CRS (EPSG:3857) then reproject to EPSG:4326
# so all layers actually align on the map.
def fix_crs(gdf):
    gdf = gdf.set_crs("EPSG:3857", allow_override=True)
    return gdf.to_crs("EPSG:4326")

poverty_gdf     = fix_crs(poverty_gdf)
consumption_gdf = fix_crs(consumption_gdf)

# Stringify all non-geometry columns to prevent JSON serialization errors
# (done AFTER CRS fix, and we keep numeric copies of key fields before stringifying)
poverty_gdf['av_CONS_numeric']     = pd.to_numeric(consumption_gdf['av_CONS'], errors='coerce')
poverty_gdf['Per_Tot_Po_numeric']  = pd.to_numeric(poverty_gdf['Per_Tot_Po'], errors='coerce')
consumption_gdf['av_CONS_numeric'] = pd.to_numeric(consumption_gdf['av_CONS'], errors='coerce')

for df in [parish_gdf, poverty_gdf, consumption_gdf, grid_gdf]:
    for col in df.columns:
        if col != 'geometry' and not col.endswith('_numeric'):
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
def normalize_parish(name):
    if not isinstance(name, str):
        return ""
    cleaned = name.upper().replace(".", "")
    cleaned = cleaned.replace("SAINT", "ST")
    return " ".join(cleaned.split())

parish_gdf['_parish_key']      = parish_gdf['name'].apply(normalize_parish)
poverty_gdf['_parish_key']     = poverty_gdf['PARISH'].apply(normalize_parish)
consumption_gdf['_parish_key'] = consumption_gdf['PARISH'].apply(normalize_parish)
if 'Parish' in dwel_df.columns:
    dwel_df['_parish_key'] = dwel_df['Parish'].apply(normalize_parish)

parish_list = sorted(parish_gdf['name'].dropna().unique().tolist())
parish_name_to_key = dict(zip(parish_gdf['name'], parish_gdf['_parish_key']))

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

def update_communities(event):
    community_selector.options = get_community_list(event.new)
    community_selector.value = 'All Communities'

parish_selector.param.watch(update_communities, 'value')

# --- 3. Map Builder ---
def build_map(selected_parish, selected_community):
    m = folium.Map(location=[18.15, -77.3], zoom_start=9, tiles="CartoDB dark_matter")

    # Parish Boundaries — gray
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

    # Communities — filtered, now correctly reprojected
    if selected_parish == 'Island-Wide':
        communities_to_show = poverty_gdf
    else:
        key = parish_name_to_key.get(selected_parish)
        communities_to_show = poverty_gdf[poverty_gdf['_parish_key'] == key]

    if selected_community != 'All Communities' and 'COMM_NAME' in communities_to_show.columns:
        communities_to_show = communities_to_show[
            communities_to_show['COMM_NAME'] == selected_community
        ]

    tooltip_fields  = [c for c in ['COMM_NAME', 'PARISH'] if c in poverty_gdf.columns]
    tooltip_aliases = ['Community:', 'Parish:'][:len(tooltip_fields)]

    if len(communities_to_show) > 0:
        folium.GeoJson(
            communities_to_show,
            name="Communities",
            style_function=lambda x: {
                'color': '#00E5FF',
                'fillColor': '#00E5FF',
                'fillOpacity': 0.15,
                'weight': 1.5,
                'opacity': 0.9
            },
            highlight_function=lambda x: {'weight': 3, 'fillOpacity': 0.35},
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

    # --- ZOOM TO SELECTED PARISH ---
    if selected_parish != 'Island-Wide':
        match = parish_gdf[parish_gdf['name'] == selected_parish]
        if len(match) > 0:
            minx, miny, maxx, maxy = match.total_bounds
            # folium wants [[south, west], [north, east]] i.e. [[lat,lon],[lat,lon]]
            m.fit_bounds([[miny, minx], [maxy, maxx]])

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

# --- 7. Reactive Bottom Metrics Bar ---
def lookup_dwelling_row(parish_display_name, community_name):
    if 'Community' not in dwel_df.columns:
        return None
    key = parish_name_to_key.get(parish_display_name)
    subset = dwel_df[dwel_df['_parish_key'] == key] if key and '_parish_key' in dwel_df.columns else dwel_df
    match = subset[subset['Community'].astype(str).str.strip().str.upper() == community_name.strip().upper()]
    return match.iloc[0] if len(match) > 0 else None

def lookup_poverty_row(parish_display_name, community_name):
    key = parish_name_to_key.get(parish_display_name)
    subset = poverty_gdf[poverty_gdf['_parish_key'] == key] if key else poverty_gdf
    if 'COMM_NAME' not in subset.columns:
        return None
    match = subset[subset['COMM_NAME'].astype(str).str.strip().str.upper() == community_name.strip().upper()]
    return match.iloc[0] if len(match) > 0 else None

def lookup_consumption_row(parish_display_name, community_name):
    key = parish_name_to_key.get(parish_display_name)
    subset = consumption_gdf[consumption_gdf['_parish_key'] == key] if key else consumption_gdf
    if 'COMM_NAME' not in subset.columns:
        return None
    match = subset[subset['COMM_NAME'].astype(str).str.strip().str.upper() == community_name.strip().upper()]
    return match.iloc[0] if len(match) > 0 else None

def stat_block(label, value, sublabel="", color="#fff"):
    sub_html = f'<div style="color:#8fa8b8; font-size:9px;">{sublabel}</div>' if sublabel else ""
    return f"""
        <div>
            <div style="color:#8fa8b8; font-size:10px; margin-bottom:4px;">{label}</div>
            <div style="color:{color}; font-size:28px; font-weight:700;">{value}</div>
            {sub_html}
        </div>"""

@pn.depends(parish_selector.param.value, community_selector.param.value)
def bottom_bar(selected_parish, selected_community):
    if selected_parish == 'Island-Wide' or selected_community == 'All Communities':
        scope_label = "Island-Wide" if selected_parish == 'Island-Wide' else selected_parish
        html = f"""
        <div style="background:#0f2535; padding:32px 32px; border-top:2px solid #F5A623;
                    font-family:monospace; width:100%; box-sizing:border-box;">
            <div style="color:#F5A623; font-size:11px; text-transform:uppercase;
                        letter-spacing:2px; margin-bottom:20px;">{scope_label} Snapshot</div>
            <div style="display:flex; gap:40px; flex-wrap:wrap; margin-bottom:28px;">
                {stat_block("Total Parishes", "14")}
                {stat_block("Population (2021)", "2.73M")}
                {stat_block("Grid Connected", "~96%")}
                {stat_block("Renewables Share", "~15%")}
                {stat_block("Vision 2030 Target", "50%", color="#F5A623")}
                {stat_block("JPS Blended Rate", f"${JPS_BLENDED_RATE_JMD_PER_KWH:.2f} JMD/kWh", sublabel="Late 2025 / early 2026 est.")}
            </div>
            <div style="color:#8fa8b8; font-size:11px;">
                Select a specific Parish and Community above to see localized data.
            </div>
        </div>"""
        return pn.pane.HTML(html, sizing_mode='stretch_width', margin=0)

    dwel_row       = lookup_dwelling_row(selected_parish, selected_community)
    poverty_row    = lookup_poverty_row(selected_parish, selected_community)
    consumption_row = lookup_consumption_row(selected_parish, selected_community)

    pop_val  = dwel_row['Total Population']    if dwel_row is not None and 'Total Population' in dwel_row else "N/A"
    dwel_val = dwel_row['Number of Dwellings'] if dwel_row is not None and 'Number of Dwellings' in dwel_row else "N/A"
    pov_val  = poverty_row['Per_Tot_Po']       if poverty_row is not None and 'Per_Tot_Po' in poverty_row else None

    cons_numeric = None
    if consumption_row is not None and 'av_CONS_numeric' in consumption_row:
        val = consumption_row['av_CONS_numeric']
        if pd.notna(val):
            cons_numeric = val

    if cons_numeric is not None:
        cons_display = f"${cons_numeric:,.0f} JMD"
        cons_sublabel = "Avg. household electricity expenditure"
    else:
        cons_display = "No data"
        cons_sublabel = "Not available for this community"

    try:
        pov_display = f"{float(pov_val):.1f}%"
    except (TypeError, ValueError):
        pov_display = "N/A"

    missing_note = ""
    if dwel_row is None or poverty_row is None or cons_numeric is None:
        missing_note = """<div style="color:#FF8C00; font-size:10px; margin-top:16px;">
            ⚠ Some data not found for this community — coverage varies by source dataset.
            </div>"""

    html = f"""
    <div style="background:#0f2535; padding:32px 32px; border-top:2px solid #F5A623;
                font-family:monospace; width:100%; box-sizing:border-box;">
        <div style="color:#F5A623; font-size:11px; text-transform:uppercase;
                    letter-spacing:2px; margin-bottom:20px;">{selected_community}, {selected_parish}</div>
        <div style="display:flex; gap:40px; flex-wrap:wrap;">
            {stat_block("Total Population", pop_val)}
            {stat_block("Number of Dwellings", dwel_val)}
            {stat_block("Avg Electricity Expenditure", cons_display, sublabel=cons_sublabel)}
            {stat_block("Poverty Rate", pov_display, color="#FF8C00")}
        </div>
        {missing_note}
    </div>"""
    return pn.pane.HTML(html, sizing_mode='stretch_width', margin=0)

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
        <div style="width:14px; height:14px; background:#00E5FF44; border:1px solid #00E5FF; margin-right:10px;"></div> Communities
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
