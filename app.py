# LAST UPDATED: 7-12-2026

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
import json
import warnings
import geopandas as gpd
import pandas as pd
import panel as pn
from ipyleaflet import Map as LeafletMap, GeoJSON, Marker, AwesomeIcon, basemaps, LayersControl, WidgetControl, LayerGroup
from ipywidgets import HTML as IPyHTML
import branca.colormap as cm

# --- Initialize Panel ---
pn.extension('ipywidgets', design='bootstrap')

base_dir = os.path.dirname(os.path.abspath(__file__)) if '__file__' in locals() else os.getcwd()

# --- JPS Blended Residential Rate (late 2025 / early 2026) ---
# Reference only — av_CONS is already in JMD, so this is NOT applied to it directly.
# NOTE: previously had a JPS_BLENDED_RATE_JMD_PER_KWH constant here ($46/kWh) as a
# stand-in for real billing data. Removed once JSLC 2023 gave us actual reported
# electricity bills (electric field) — the estimate had no traceable citation and
# is no longer needed now that real survey data exists.

# --- 1. Load Local Data ---
# NOTE: filenames were originally swapped at the source (grab_data.py scrape) —
# jamaica_consumption_2012.geojson now correctly holds av_CONS (consumption),
# jamaica_poverty_2012.geojson now correctly holds Per_Tot_Po (poverty rate).
# Rename on disk with:
#   mv assets/jamaica_consumption_2012.geojson assets/jamaica_consumption_2012_TEMP.geojson
#   mv assets/jamaica_poverty_2012.geojson assets/jamaica_consumption_2012.geojson
#   mv assets/jamaica_consumption_2012_TEMP.geojson assets/jamaica_poverty_2012.geojson
parish_gdf      = gpd.read_file(os.path.join(base_dir, "assets", "ja_parishes.json"))
poverty_gdf     = gpd.read_file(os.path.join(base_dir, "assets", "jamaica_poverty_2012.geojson"))
consumption_gdf = gpd.read_file(os.path.join(base_dir, "assets", "jamaica_consumption_2012.geojson"))
grid_gdf        = gpd.read_file(os.path.join(base_dir, "assets", "overpass_energy_infra_2.geojson"))
dwel_df         = pd.read_csv(os.path.join(base_dir, "assets", "PopulationandDwellingCountsbyCommunity_.csv"))
burden_df       = pd.read_csv(os.path.join(base_dir, "assets", "jslc_2023_parish_burden.csv"))

# Normalize burden_df's parish names to match the same key system as everything else
burden_df['_parish_key'] = burden_df['parish_name'].apply(
    lambda x: " ".join(str(x).upper().replace(".", "").replace("SAINT", "ST").split())
)

# --- CRS FIX ---
# poverty_gdf / consumption_gdf report .crs as EPSG:4326 but coordinate VALUES are
# actually in Web Mercator meters. Force-assign the real CRS, then reproject properly.
def fix_crs(gdf):
    gdf = gdf.set_crs("EPSG:3857", allow_override=True)
    return gdf.to_crs("EPSG:4326")

poverty_gdf     = fix_crs(poverty_gdf)
consumption_gdf = fix_crs(consumption_gdf)

# Keep numeric copies of key fields before the stringify pass below
poverty_gdf['Per_Tot_Po_numeric']  = pd.to_numeric(poverty_gdf['Per_Tot_Po'], errors='coerce')
consumption_gdf['av_CONS_numeric'] = pd.to_numeric(consumption_gdf['av_CONS'], errors='coerce')

for df in [parish_gdf, poverty_gdf, consumption_gdf, grid_gdf]:
    for col in df.columns:
        if col != 'geometry' and not col.endswith('_numeric'):
            df[col] = df[col].astype(str)

# Filter to valid geometry types only
valid_types = ['Point', 'MultiPoint', 'LineString', 'MultiLineString', 'Polygon', 'MultiPolygon']
grid_gdf = grid_gdf[grid_gdf.geometry.geom_type.isin(valid_types)].copy()

transmission_gdf = grid_gdf[grid_gdf['power'] == 'line'].copy()

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

# Join burden values onto parish_gdf once, for the choropleth layer.
# parish_gdf['_parish_key'] was stringified earlier in the stringify pass, so this
# merge happens on the string key column, which is fine since both sides match.
parish_gdf = parish_gdf.merge(
    burden_df[['_parish_key', 'avg_burden_pct']],
    on='_parish_key',
    how='left'
)

def get_community_list(selected_parish):
    if selected_parish == 'Island-Wide':
        return ['All Communities']
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
    m = LeafletMap(center=(18.15, -77.3), zoom=9, basemap=basemaps.CartoDB.DarkMatter,
                   prefer_canvas=True)
    # prefer_canvas=True: Leaflet's SVG renderer has documented, long-standing bugs
    # where polygon hover/click hit-detection breaks after the map pans or zooms
    # (Leaflet issues #5773, #6142) — exactly what fit_bounds() does below every
    # time a parish is selected. Canvas rendering avoids this entirely and also
    # handles 100+ overlapping small polygons more reliably than SVG DOM paths.
    m.layout.height = "500px"

    # info_box is created here but added to the map AFTER LayersControl below,
    # so it doesn't show up as an unnamed ghost entry in the layer switcher.
    info_box = IPyHTML(value="<i>Hover over the map for details</i>")
    info_box.layout.margin = "0px 10px 10px 10px"

    # --- Parish Boundaries — always visible, gray ---
    parish_geojson_data = json.loads(parish_gdf.to_json())
    parish_layer = GeoJSON(
        data=parish_geojson_data,
        name="Parish Boundaries",
        style={'color': '#A0A0A0', 'fillColor': '#000000', 'fillOpacity': 0.05, 'weight': 1.5},
        hover_style={'color': '#F5A623', 'weight': 2.5, 'fillOpacity': 0.1},
    )

    def parish_hover(feature, **kwargs):
        name = feature['properties'].get('name', 'Unknown')
        info_box.value = f"<b style='color:#F5A623'>Parish:</b> {name}"

    parish_layer.on_hover(parish_hover)
    m.add(parish_layer)

    # --- Zoom to selected parish — moved here, BEFORE the community layer is
    # added below. Leaflet has documented bugs (#5773, #6142) where polygon
    # hover/click hit-detection breaks if the map pans/zooms AFTER an
    # interactive layer is added. Panning first, then adding the community
    # layer once the view has settled, avoids that entirely.
    if selected_parish != 'Island-Wide':
        match = parish_gdf[parish_gdf['name'] == selected_parish]
        if len(match) > 0:
            minx, miny, maxx, maxy = match.total_bounds
            m.fit_bounds([[miny, minx], [maxy, maxx]])

    # --- Energy Burden Choropleth (parish-level, JSLC 2023) — Island-Wide only ---
    if selected_parish == 'Island-Wide':
        burden_min = parish_gdf['avg_burden_pct'].min()
        burden_max = parish_gdf['avg_burden_pct'].max()
        burden_colormap = cm.LinearColormap(
            colors=['#1a3a4a', '#F5A623', '#FF3D00'],
            vmin=burden_min,
            vmax=burden_max
        )

        # FIX: ipyleaflet's style_callback computes styles once at construction
        # and doesn't reliably re-propagate to the rendered Leaflet layer — this
        # is a long-documented ipyleaflet limitation (GitHub issues #227, #341,
        # #607, #675), not something fixable from our side via style_callback.
        # The reliable approach is to bake the computed color directly into each
        # feature's properties.style BEFORE constructing the GeoJSON layer.
        def compute_burden_color(val):
            if val is None:
                return {'fillColor': '#444444', 'color': '#A0A0A0', 'weight': 1, 'fillOpacity': 0.5}
            return {'fillColor': burden_colormap(val), 'color': '#0f2535', 'weight': 1, 'fillOpacity': 0.7}

        burden_geojson_data = json.loads(json.dumps(parish_geojson_data))  # deep copy
        for feat in burden_geojson_data['features']:
            val = feat['properties'].get('avg_burden_pct')
            feat['properties']['style'] = compute_burden_color(val)

        burden_layer = GeoJSON(
            data=burden_geojson_data,
            name="Energy Burden (%, JSLC 2023)",
            hover_style={'weight': 3, 'color': '#FFFFFF'},
            visible=False  # off by default; person toggles it on via layer control
        )

        def burden_hover(feature, **kwargs):
            name = feature['properties'].get('name', 'Unknown')
            val = feature['properties'].get('avg_burden_pct')
            val_display = f"{val:.1f}%" if val is not None else "N/A"
            info_box.value = f"<b style='color:#F5A623'>Parish:</b> {name}<br><b style='color:#FF8C00'>Avg Energy Burden:</b> {val_display}"

        burden_layer.on_hover(burden_hover)
        m.add(burden_layer)

    # --- Communities: only render once a specific parish is chosen ---
    if selected_parish != 'Island-Wide':
        key = parish_name_to_key.get(selected_parish)
        communities_to_show = poverty_gdf[poverty_gdf['_parish_key'] == key].copy()

        if selected_community != 'All Communities' and 'COMM_NAME' in communities_to_show.columns:
            communities_to_show = communities_to_show[
                communities_to_show['COMM_NAME'] == selected_community
            ]

        # Always-real display name, never blank — unnamed ones get a stable
        # index (#1, #2...) instead of a repeated generic label, so hovering
        # between two different unnamed communities visibly shows different
        # text, proving hover is per-feature rather than stuck on one shape.
        communities_to_show = communities_to_show.reset_index(drop=True)
        communities_to_show['_comm_display'] = [
            row['COMM_NAME'] if isinstance(row['COMM_NAME'], str) and row['COMM_NAME'].strip() not in ('', 'nan', 'None')
            else f'(Unnamed community #{i+1})'
            for i, row in communities_to_show.iterrows()
        ]

        if len(communities_to_show) > 0:
            communities_geojson_data = json.loads(communities_to_show.to_json())

            pov_min = communities_to_show['Per_Tot_Po_numeric'].min()
            pov_max = communities_to_show['Per_Tot_Po_numeric'].max()
            has_poverty_gradient = pd.notna(pov_min) and pd.notna(pov_max) and pov_min != pov_max

            # ONE layer, matching the exact structure of the working parish
            # burden layer: baked colors in properties.style, hover_style on
            # THIS layer, on_hover/on_click on THIS SAME layer. No separate
            # transparent overlay — that split was the actual bug causing both
            # the blob hover and the washed colors.
            if has_poverty_gradient:
                poverty_colormap = cm.LinearColormap(
                    colors=['#1a3a4a', '#F5A623', '#FF3D00'],
                    vmin=pov_min,
                    vmax=pov_max
                )
                for feat in communities_geojson_data['features']:
                    val = feat['properties'].get('Per_Tot_Po_numeric')
                    is_unnamed = str(feat['properties'].get('_comm_display', '')).startswith('(Unnamed')
                    if val is None or (isinstance(val, float) and val != val):
                        feat['properties']['style'] = {'fillColor': '#2a2a2a', 'color': '#666666',
                                                        'weight': 1, 'fillOpacity': 0.6,
                                                        'dashArray': '3, 3' if is_unnamed else None}
                    else:
                        try:
                            feat['properties']['style'] = {'fillColor': poverty_colormap(float(val)),
                                                            'color': '#0f2535', 'weight': 1, 'fillOpacity': 0.85,
                                                            'dashArray': '3, 3' if is_unnamed else None}
                        except (TypeError, ValueError):
                            feat['properties']['style'] = {'fillColor': '#2a2a2a', 'color': '#666666',
                                                            'weight': 1, 'fillOpacity': 0.6,
                                                            'dashArray': '3, 3' if is_unnamed else None}
            else:
                for feat in communities_geojson_data['features']:
                    feat['properties']['style'] = {'fillColor': '#00E5FF', 'color': '#00E5FF',
                                                    'fillOpacity': 0.12, 'weight': 1.5}

            community_layer = GeoJSON(
                data=communities_geojson_data,
                name="Communities",
                hover_style={'weight': 3, 'color': '#FFFFFF'},
            )

            def community_hover(feature, **kwargs):
                name = feature['properties'].get('_comm_display', 'Unknown')
                parish = feature['properties'].get('PARISH', '')
                val = feature['properties'].get('Per_Tot_Po_numeric')
                pov_str = (f"<br><b style='color:#F5A623'>Poverty Rate:</b> {val:.1f}%"
                           if val is not None and isinstance(val, float) and val == val else "")
                info_box.value = (
                    f"<b style='color:#F5A623'>Community:</b> {name}"
                    f"<br><b style='color:#F5A623'>Parish:</b> {parish}"
                    f"{pov_str}"
                    f"<br><i style='font-size:11px; color:#8fa8b8'>Click to select →</i>"
                )

            def community_click(feature, **kwargs):
                name = feature['properties'].get('COMM_NAME')
                if isinstance(name, str) and name.strip() not in ('', 'nan', 'None'):
                    community_selector.value = name

            community_layer.on_hover(community_hover)
            community_layer.on_click(community_click)
            m.add(community_layer)

    # --- Transmission Lines — gold, always visible ---
    transmission_geojson_data = json.loads(transmission_gdf.to_json())
    transmission_layer = GeoJSON(
        data=transmission_geojson_data,
        name="Transmission Lines",
        style={'color': '#F5A623', 'weight': 2.0},
    )
    m.add(transmission_layer)

    # --- Power Plants — wrapped in LayerGroup so they appear as ONE entry
    # in LayersControl, not as individual unnamed toggleable layers.
    plant_markers = []
    for _, row in plants_gdf.iterrows():
        plant_name = row.get('name', '')
        if not isinstance(plant_name, str) or plant_name.strip().lower() in ('', 'nan', 'none'):
            continue
        source   = row.get('plant:source', 'Unknown')
        capacity = row.get('plant:output:electricity', 'Unknown')
        popup_html = IPyHTML(value=f"<b style='color:#FF3D00'>{plant_name}</b><br>Fuel: {source}<br>Capacity: {capacity}")
        marker = Marker(
            location=(row.geometry.y, row.geometry.x),
            icon=AwesomeIcon(name='bolt', marker_color='red', icon_color='white'),
            draggable=False,
        )
        marker.popup = popup_html
        plant_markers.append(marker)
    m.add(LayerGroup(layers=plant_markers, name="Power Plants"))

    m.add(LayersControl(position='topright'))
    # Add info_box AFTER LayersControl — WidgetControls added before LayersControl
    # show up as unnamed ghost entries in the layer switcher list.
    m.add(WidgetControl(widget=info_box, position='bottomright'))

    return m

# --- 4. Reactive Map ---
@pn.depends(parish_selector.param.value, community_selector.param.value)
def map_view(selected_parish, selected_community):
    m = build_map(selected_parish, selected_community)
    return pn.pane.IPyWidget(m, min_height=500, sizing_mode='stretch_both')

# --- 5. Reactive Parish Info ---
@pn.depends(parish_selector.param.value, community_selector.param.value)
def parish_info(selected_parish, selected_community):
    if selected_parish == 'Island-Wide':
        text = "Viewing all 14 parishes. Select a parish to see its communities on the map."
    else:
        key = parish_name_to_key.get(selected_parish)
        subset = poverty_gdf[poverty_gdf['_parish_key'] == key]
        n = len(subset)
        if selected_community != 'All Communities':
            text = f"**{selected_parish} → {selected_community}**\n\nZoomed to selected community."
        else:
            text = f"**{selected_parish}**\n\n{n} communities now visible on map. Hover to see details."
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

def lookup_burden_row(parish_display_name):
    """JSLC 2023 parish-level energy burden — real survey data, not modeled."""
    key = parish_name_to_key.get(parish_display_name)
    if key is None:
        return None
    match = burden_df[burden_df['_parish_key'] == key]
    return match.iloc[0] if len(match) > 0 else None

# Island-wide average burden, computed once at startup from the JSLC 2023 parish table.
ISLAND_AVG_BURDEN = burden_df['avg_burden_pct'].mean()

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
    # --- Island-wide: real JSLC 2023 island average ---
    if selected_parish == 'Island-Wide':
        html = f"""
        <div style="background:#0f2535; padding:32px 32px; border-top:2px solid #F5A623;
                    font-family:monospace; width:100%; box-sizing:border-box;">
            <div style="color:#F5A623; font-size:11px; text-transform:uppercase;
                        letter-spacing:2px; margin-bottom:20px;">Island-Wide Snapshot</div>
            <div style="display:flex; gap:40px; flex-wrap:wrap; margin-bottom:28px;">
                {stat_block("Total Parishes", "14")}
                {stat_block("Population (2021)", "2.73M")}
                {stat_block("Grid Connected", "~96%")}
                {stat_block("Renewables Share", "~15%")}
                {stat_block("Vision 2030 Target", "50%", color="#F5A623")}
                {stat_block("Avg Energy Burden", f"{ISLAND_AVG_BURDEN:.1f}%", sublabel="JSLC 2023 survey, 14-parish avg", color="#FF8C00")}
            </div>
            <div style="color:#8fa8b8; font-size:11px;">
                Select a specific Parish above to see that parish's energy burden, or drill into a Community for local data.
            </div>
        </div>"""
        return pn.pane.HTML(html, sizing_mode='stretch_width', margin=0)

    # --- Parish selected, no specific community: show real parish burden ---
    if selected_community == 'All Communities':
        burden_row = lookup_burden_row(selected_parish)
        if burden_row is not None:
            burden_display = f"{burden_row['avg_burden_pct']:.1f}%"
            n_hh = int(burden_row['n_households'])
            elec_avg = burden_row['avg_electric_bill']
            exp_avg = burden_row['avg_tot_exp']
        else:
            burden_display = "N/A"
            n_hh = 0
            elec_avg = None
            exp_avg = None

        html = f"""
        <div style="background:#0f2535; padding:32px 32px; border-top:2px solid #F5A623;
                    font-family:monospace; width:100%; box-sizing:border-box;">
            <div style="color:#F5A623; font-size:11px; text-transform:uppercase;
                        letter-spacing:2px; margin-bottom:20px;">{selected_parish} Snapshot</div>
            <div style="display:flex; gap:40px; flex-wrap:wrap; margin-bottom:28px;">
                {stat_block("Avg Energy Burden", burden_display, sublabel=f"JSLC 2023, n={n_hh} households", color="#FF8C00")}
                {stat_block("Avg Electricity Bill", f"${elec_avg:,.0f} JMD/yr" if elec_avg is not None else "N/A", sublabel="JSLC 2023 survey")}
                {stat_block("Avg Total Expenditure", f"${exp_avg:,.0f} JMD/yr" if exp_avg is not None else "N/A", sublabel="JSLC 2023 survey")}
                {stat_block("Households Surveyed", f"{n_hh}", sublabel="JSLC 2023 sample size, this parish")}
            </div>
            <div style="color:#8fa8b8; font-size:11px;">
                Select a Community above to see community-level consumption and poverty data (2012 small-area model).
            </div>
        </div>"""
        return pn.pane.HTML(html, sizing_mode='stretch_width', margin=0)

    dwel_row        = lookup_dwelling_row(selected_parish, selected_community)
    poverty_row     = lookup_poverty_row(selected_parish, selected_community)
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
        cons_sublabel = "Est. annual household consumption (2012 small-area model)"
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
            {stat_block("Avg Household Consumption", cons_display, sublabel=cons_sublabel)}
            {stat_block("Poverty Rate", pov_display, color="#FF8C00")}
        </div>
        {missing_note}
    </div>"""
    return pn.pane.HTML(html, sizing_mode='stretch_width', margin=0)

# --- 8. Legend ---
def static_legend_top():
    """Top portion of the legend — same for every view."""
    return """
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
    <div style="font-size:10px; color:#8fa8b8; margin-top:8px; font-style:italic;">
        Click a community on the map to select it in the dropdown.
    </div>"""

def choropleth_gradient_bar(colors, vmin, vmax, label):
    """Builds a small CSS gradient bar with min/max labels — a lightweight
    stand-in for the on-map colormap legend, since that lives inside the
    ipyleaflet widget itself and isn't easy to read out into Panel's sidebar."""
    gradient_css = f"linear-gradient(to right, {', '.join(colors)})"
    return f"""
    <div style="margin-top:10px; padding-top:10px; border-top:1px solid #2a4a5a;">
        <div style="color:#F5A623; font-size:11px; text-transform:uppercase; letter-spacing:1px; margin-bottom:6px;">{label}</div>
        <div style="height:14px; border-radius:3px; background:{gradient_css}; margin-bottom:4px;"></div>
        <div style="display:flex; justify-content:space-between; font-size:10px; color:#8fa8b8;">
            <span>{vmin:.1f}%</span>
            <span>{vmax:.1f}%</span>
        </div>
    </div>"""

@pn.depends(parish_selector.param.value, community_selector.param.value)
def reactive_legend(selected_parish, selected_community):
    top = static_legend_top()

    if selected_parish == 'Island-Wide':
        burden_min = parish_gdf['avg_burden_pct'].min()
        burden_max = parish_gdf['avg_burden_pct'].max()
        gradient = choropleth_gradient_bar(
            ['#1a3a4a', '#F5A623', '#FF3D00'], burden_min, burden_max,
            "Energy Burden % (toggle layer on map)"
        )
    else:
        key = parish_name_to_key.get(selected_parish)
        subset = poverty_gdf[poverty_gdf['_parish_key'] == key]
        pov_min = subset['Per_Tot_Po_numeric'].min()
        pov_max = subset['Per_Tot_Po_numeric'].max()
        if pd.notna(pov_min) and pd.notna(pov_max) and pov_min != pov_max:
            gradient = choropleth_gradient_bar(
                ['#00B8D4', '#FFD600', '#FF1744'], pov_min, pov_max,
                f"Poverty Rate % — {selected_parish}"
            )
        else:
            gradient = """<div style="margin-top:10px; padding-top:10px; border-top:1px solid #2a4a5a;
                font-size:10px; color:#8fa8b8; font-style:italic;">
                No poverty rate gradient available for this parish.</div>"""

    full_html = top + gradient + "\n</div>"
    return pn.pane.HTML(full_html, width=280)

# --- 9. Reactive Energy Burden Card ---
# Real JSLC 2023 data — replaces the placeholder. Updates with parish selection;
# shows island-wide average when no parish is chosen.

@pn.depends(parish_selector.param.value)
def energy_burden_card(selected_parish):
    if selected_parish == 'Island-Wide':
        value = f"{ISLAND_AVG_BURDEN:.1f}%"
        label = "Energy Burden (Island Avg)"
    else:
        row = lookup_burden_row(selected_parish)
        if row is not None:
            value = f"{row['avg_burden_pct']:.1f}%"
            label = f"Energy Burden ({selected_parish})"
        else:
            value = "N/A"
            label = "Energy Burden"
    return metric_card(label, value, color="#FF8C00")

# --- 10. Sidebar ---
sidebar_content = pn.Column(
    pn.pane.Markdown("## BSC Metrics", styles={"color": "#F5A623"}),
    energy_burden_card,
    metric_card("Overall Resiliency", "—"),
    metric_card("Energy Justice",     "—"),
    metric_card("Carbon Intensity",   "—"),
    pn.layout.Divider(),
    parish_selector,
    community_selector,
    parish_info,
    pn.layout.Divider(),
    reactive_legend,
    width=300,
)

# --- 11. Main Layout ---
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
