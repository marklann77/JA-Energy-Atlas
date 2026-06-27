# LAST UPDATED: 6-27-2026

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
import branca.colormap as cm

# --- Initialize Panel ---
pn.extension(design='bootstrap')

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
    m = folium.Map(location=[18.15, -77.3], zoom_start=9, tiles="CartoDB dark_matter")

    # Parish Boundaries — always visible, gray
    # NOTE: fillOpacity=0 means no fill, but Leaflet still needs SOME fill to register
    # hover/mouse events reliably across the whole polygon (not just the border line).
    # fillOpacity=0.01 is visually identical to 0 but keeps hover detection working
    # everywhere inside the parish shape, not just exactly on the 1.5px border.
    folium.GeoJson(
        parish_gdf,
        name="Parish Boundaries",
        style_function=lambda x: {'color': '#A0A0A0', 'fillOpacity': 0.01, 'fillColor': '#000000', 'weight': 1.5},
        highlight_function=lambda x: {'weight': 2.5, 'color': '#F5A623'},
        tooltip=folium.GeoJsonTooltip(
            fields=['name'],
            aliases=['Parish:'],
            style="background-color:#0f2535; color:#ffffff; font-family:monospace;",
            sticky=True
        )
    ).add_to(m)

    # --- Energy Burden Choropleth (parish-level, JSLC 2023) ---
    # Only shown at Island-Wide — once a single parish is selected, comparing
    # 14 colors collapses to "this one parish, one shade," which isn't useful.
    # The Poverty Rate choropleth below takes over at that zoom level instead.
    if selected_parish == 'Island-Wide':
        burden_min = parish_gdf['avg_burden_pct'].min()
        burden_max = parish_gdf['avg_burden_pct'].max()
        burden_colormap = cm.LinearColormap(
            colors=['#1a3a4a', '#F5A623', '#FF3D00'],
            vmin=burden_min,
            vmax=burden_max
        )

        def burden_style(feature):
            val = feature['properties'].get('avg_burden_pct')
            if val is None:
                return {'fillColor': '#444444', 'color': '#A0A0A0', 'weight': 1, 'fillOpacity': 0.5}
            return {
                'fillColor': burden_colormap(val),
                'color': '#0f2535',
                'weight': 1,
                'fillOpacity': 0.7
            }

        folium.GeoJson(
            parish_gdf,
            name="Energy Burden (%, JSLC 2023)",
            style_function=burden_style,
            highlight_function=lambda x: {'weight': 3, 'color': '#FFFFFF'},
            tooltip=folium.GeoJsonTooltip(
                fields=['name', 'avg_burden_pct'],
                aliases=['Parish:', 'Avg Energy Burden (%):'],
                style="background-color:#0f2535; color:#F5A623; font-family:monospace;",
                sticky=True
            ),
            show=False  # off by default; person toggles it on via layer control
        ).add_to(m)
        burden_colormap.caption = 'Avg. Energy Burden (% of household expenditure) — JSLC 2023'
        burden_colormap.add_to(m)

    # --- OPTION A: Communities only render once a specific parish is chosen ---
    if selected_parish != 'Island-Wide':
        key = parish_name_to_key.get(selected_parish)
        communities_to_show = poverty_gdf[poverty_gdf['_parish_key'] == key]

        if selected_community != 'All Communities' and 'COMM_NAME' in communities_to_show.columns:
            communities_to_show = communities_to_show[
                communities_to_show['COMM_NAME'] == selected_community
            ]

        tooltip_fields  = [c for c in ['COMM_NAME', 'PARISH'] if c in poverty_gdf.columns]
        tooltip_aliases = ['Community:', 'Parish:'][:len(tooltip_fields)]

        # Popup fields: shown on CLICK, separate from the hover tooltip above.
        # Pulls whatever's available on this feature — COMM_NAME/PARISH always
        # exist; Per_Tot_Po (poverty rate) only if present in this gdf.
        popup_fields  = [c for c in ['COMM_NAME', 'PARISH', 'Per_Tot_Po'] if c in poverty_gdf.columns]
        popup_aliases = ['Community:', 'Parish:', 'Poverty Rate (%):'][:len(popup_fields)]

        # --- Poverty Rate Choropleth (community-level, 2012 small-area model) ---
        # Takes over the "compare across geography" job that the burden choropleth
        # did at Island-Wide — but at community granularity within this one parish.
        # Separate toggleable layer from the plain cyan "Communities" outline below.
        if len(communities_to_show) > 0 and 'Per_Tot_Po_numeric' in communities_to_show.columns:
            pov_min = communities_to_show['Per_Tot_Po_numeric'].min()
            pov_max = communities_to_show['Per_Tot_Po_numeric'].max()
            if pd.notna(pov_min) and pd.notna(pov_max) and pov_min != pov_max:
                poverty_colormap = cm.LinearColormap(
                    colors=['#1a3a4a', '#F5A623', '#FF3D00'],
                    vmin=pov_min,
                    vmax=pov_max
                )

                def poverty_style(feature):
                    val = feature['properties'].get('Per_Tot_Po')
                    try:
                        val = float(val)
                    except (TypeError, ValueError):
                        return {'fillColor': '#444444', 'color': '#A0A0A0', 'weight': 1, 'fillOpacity': 0.5}
                    return {
                        'fillColor': poverty_colormap(val),
                        'color': '#0f2535',
                        'weight': 1,
                        'fillOpacity': 0.7
                    }

                folium.GeoJson(
                    communities_to_show,
                    name="Poverty Rate (%, 2012 model)",
                    style_function=poverty_style,
                    highlight_function=lambda x: {'weight': 3, 'color': '#FFFFFF'},
                    tooltip=folium.GeoJsonTooltip(
                        fields=tooltip_fields + (['Per_Tot_Po'] if 'Per_Tot_Po' in poverty_gdf.columns else []),
                        aliases=tooltip_aliases + (['Poverty Rate (%):'] if 'Per_Tot_Po' in poverty_gdf.columns else []),
                        style="background-color:#0f2535; color:#F5A623; font-family:monospace;",
                        sticky=True
                    ),
                    show=False  # off by default; person toggles it on via layer control
                ).add_to(m)
                poverty_colormap.caption = f'Poverty Rate (% of population) — {selected_parish}, 2012 model'
                poverty_colormap.add_to(m)

        if len(communities_to_show) > 0:
            folium.GeoJson(
                communities_to_show,
                name="Communities",
                style_function=lambda x: {
                    'color': '#00E5FF',
                    'fillColor': '#00E5FF',
                    'fillOpacity': 0.12,
                    'weight': 1.5,
                    'opacity': 0.9
                },
                # Hover highlight — thickens border and brightens fill on mouseover.
                highlight_function=lambda x: {'weight': 3, 'fillOpacity': 0.4, 'color': '#FFFFFF'},
                tooltip=folium.GeoJsonTooltip(
                    fields=tooltip_fields,
                    aliases=tooltip_aliases,
                    style="background-color:#0f2535; color:#F5A623; font-family:monospace;",
                    sticky=True
                ),
                # CLICK on a community opens this popup with its stats.
                # This does NOT talk back to the Panel dropdown (that would need a
                # JS<->Python bridge) — it's map-only, but reliable and simple.
                popup=folium.GeoJsonPopup(
                    fields=popup_fields,
                    aliases=popup_aliases,
                    localize=True,
                    labels=True,
                    style="background-color:#0f2535; color:#F5A623; font-family:monospace; border:1px solid #00E5FF;"
                )
            ).add_to(m)

    # Transmission Lines — gold, always visible
    folium.GeoJson(
        transmission_gdf,
        name="Transmission Lines",
        style_function=lambda x: {'color': '#F5A623', 'weight': 2.0}
    ).add_to(m)

    # Power Plants — always visible
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

    # Zoom to selected parish
    if selected_parish != 'Island-Wide':
        match = parish_gdf[parish_gdf['name'] == selected_parish]
        if len(match) > 0:
            minx, miny, maxx, maxy = match.total_bounds
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
        <div style="width:14px; height:14px; background:#00E5FF44; border:1px solid #00E5FF; margin-right:10px;"></div> Communities (select a parish)
    </div>
</div>"""

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
    pn.pane.HTML(legend_html, width=280),
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
