-- corona_replace.ms
-- Kor detta INNE I MAX, med scenen oppen (samma .max-fil som du exporterade fran).
-- Laser corona_data.ms (skapad av corona_export.py) och byter material pa objekten
-- till PhysicalMaterial.

-- ===== INSTALLNINGAR (andra dessa) =====
coronaDataFile = @"C:\temp\corona_data.ms"
textureDirs = #()      -- extra mappar, t.ex. #(@"D:\texturer\", @"E:\maps\")
-- Scenens egen mapp och mapparna direkt under den (t.ex. "Maps") soks automatiskt.
-- =======================================

fn withSlash p =
(
    if p.count > 0 and ((substring p p.count 1) != "\\") then (p + "\\") else p
)

fn buildSearchDirs =
(
    local dirs = #()
    if maxFilePath != "" do append dirs (withSlash maxFilePath)
    for t in textureDirs do append dirs (withSlash t)
    local extra = #()
    for dd in dirs do
    (
        for sd in (getDirectories (dd + "*")) do append extra sd
    )
    for e in extra do append dirs e
    dirs
)

searchDirs = buildSearchDirs()

fn findTex f =
(
    local result = f
    local found = false
    for dd in searchDirs while not found do
    (
        local p = dd + f
        if doesFileExist p do
        (
            result = p
            found = true
        )
    )
    if not found do format "  OBS: hittade inte texturen %\n" f
    result
)

fileIn coronaDataFile   -- skapar listan coronaList

mtlCache = Dictionary #string
replaced = 0

for item in coronaList do
(
    local o = getNodeByName item[1]
    local d = item[2]
    if o == undefined then
        format "  OBS: hittade inget objekt som heter '%'\n" item[1]
    else
    (
        local mname = d["material"]
        local pm = mtlCache[mname]
        if pm == undefined do
        (
            pm = PhysicalMaterial name:mname
            local v

            v = d["diffuse"]
            if v != undefined do pm.base_color = color (v.x * 255) (v.y * 255) (v.z * 255)

            -- Diffuse: CoronaMix hade tva bilder. Ar den forsta en AO-bild (namn med _AO
            -- eller occlusion) multipliceras basfargen (den andra) med AO.
            local baseName = d["bitmap"]
            local aoName = undefined
            v = d["bitmap2"]
            if v != undefined and baseName != undefined do
            (
                if (matchPattern baseName pattern:"*_ao*" ignoreCase:true) or (matchPattern baseName pattern:"*occlusion*" ignoreCase:true) then
                (
                    aoName = baseName
                    baseName = v
                )
                else format "  OBS: % har tva diffuse-bilder (% och %), bara den forsta anvands\n" mname baseName v
            )
            if baseName != undefined do
            (
                local baseTex = BitmapTexture filename:(findTex baseName)
                if aoName != undefined then
                (
                    local mul = RGB_Multiply()
                    mul.map1 = baseTex
                    mul.map2 = BitmapTexture filename:(findTex aoName)
                    pm.base_color_map = mul
                )
                else pm.base_color_map = baseTex
            )

            v = d["roughness"]
            if v != undefined do pm.roughness = v

            v = d["roughmap"]
            if v != undefined do pm.roughness_map = BitmapTexture filename:(findTex v)

            v = d["metalmap"]
            if v != undefined do
            (
                pm.metalness_map = BitmapTexture filename:(findTex v)
                pm.metalness = 1.0
            )

            v = d["refraction"]
            if v != undefined do pm.trans_ior = v

            v = d["normalmap"]
            if v != undefined do
            (
                pm.bump_map = Normal_Bump normal_map:(BitmapTexture filename:(findTex v))
                v = d["strength"]
                if v != undefined do pm.bump_map_amt = v
            )

            mtlCache[mname] = pm
        )
        o.material = pm
        replaced += 1
    )
)

format "Klart: % objekt fick nytt material\n" replaced
