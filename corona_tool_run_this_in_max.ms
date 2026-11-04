-- corona_tool.ms
-- Kor INNE I MAX. Oppnar en liten dialog med knappar som
--   1) kor corona_export.py pa den scen som ar oppen just nu,
--   2) laser resultatet och byter Corona-material mot PhysicalMaterial.
-- Allt ar samlat har, corona_replace.ms behovs inte langre.

-- ===== INSTALLNINGAR (andra dessa) =====
global ct_scriptDir = @"A:\script\max\aaaa_RN\dev_rn\corona_material_import\"   -- mappen med .py-filerna
global ct_python = "python"                    -- eller full sokvag, t.ex. @"C:\Python312\python.exe"
global ct_exportScript = "corona_export.py"
global ct_dumpScript = "corona_dump.py"
global ct_textureDirs = #()                    -- extra texturmappar, t.ex. #(@"D:\tex\Maps\")
-- Opacity-kartan laggs bade i Cutout (Opacity) och i Transparency Map, sa att FStorms inbyggda
-- konverterare tar med den (FStorm gor da en FStormMix med masken). Satt till undefined for att
-- bara anvanda Cutout. Kontrollera egenskapsnamnet med showProperties (PhysicalMaterial()).
global ct_extraOpacitySlot = #transparency_map
-- Scenens egen mapp och mapparna direkt under den soks alltid automatiskt.
-- =======================================

global coronaMtls
global ct_physCache
global coronaObjs
global ct_created = 0
global ct_subReplaced = 0
global ct_searchDirs = #()
global ct_rollout

fn ct_withSlash p =
(
    if p.count > 0 and ((substring p p.count 1) != "\\") then (p + "\\") else p
)

fn ct_buildSearchDirs =
(
    local dirs = #()
    if maxFilePath != "" do append dirs (ct_withSlash maxFilePath)
    for t in ct_textureDirs do append dirs (ct_withSlash t)
    local extra = #()
    for dd in dirs do
    (
        for sd in (getDirectories (dd + "*")) do append extra sd
    )
    for e in extra do append dirs e
    dirs
)

fn ct_findTex f =
(
    local result = f
    local found = false
    for dd in ct_searchDirs while not found do
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

-- Returnerar sokvagen till scenen som ar oppen, eller undefined om den inte kan anvandas.
fn ct_prepareScene =
(
    local result = undefined
    if maxFileName == "" then
        messageBox "Spara scenen forst (den maste finnas som .max-fil pa disk)." title:"Corona -> Physical"
    else
    (
        local f = maxFilePath + maxFileName
        result = f
        if getSaveRequired() do
        (
            local ans = yesNoCancelBox "Scenen har osparade andringar. Spara forst?\n(Python laser filen sa som den ligger pa disk.)" title:"Corona -> Physical"
            if ans == #cancel then result = undefined
            else if ans == #yes then saveMaxFile f quiet:true
        )
    )
    result
)

-- Kor ett python-skript via en tillfallig .bat-fil. Utskriften sparas i outFile.
-- Returnerar exitkoden (0 = ok).
fn ct_runPython scriptName argsStr outFile =
(
    local bat = ct_scriptDir + "_ct_run.bat"
    if doesFileExist bat do deleteFile bat
    local f = createFile bat
    format "%\n" "@echo off" to:f
    format "%\n" ("cd /d \"" + ct_scriptDir + "\"") to:f
    format "%\n" ("\"" + ct_python + "\" -B -X utf8 \"" + ct_scriptDir + scriptName + "\" " + argsStr + " > \"" + outFile + "\" 2>&1") to:f
    format "%\n" "exit /b %errorlevel%" to:f
    close f
    local code = -1
    HiddenDOSCommand ("\"" + bat + "\"") startpath:ct_scriptDir exitcode:&code
    code
)

fn ct_printFile f =
(
    if doesFileExist f do
    (
        local s = openFile f mode:"r"
        if s != undefined do
        (
            while not (eof s) do format "%\n" (readLine s)
            close s
        )
    )
)

-- ===== Saknade (Corona-)material =====
fn ct_isMissing m =
(
    m != undefined and (matchPattern ((classOf m) as string) pattern:"*issing*")
)

-- Bygger ett PhysicalMaterial av datan for ett Corona-material.
fn ct_buildPhysical mname d =
(
    local pm = PhysicalMaterial name:mname
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
        local baseTex = BitmapTexture filename:(ct_findTex baseName)
        if aoName != undefined then
        (
            local mul = RGB_Multiply()
            mul.map1 = baseTex
            mul.map2 = BitmapTexture filename:(ct_findTex aoName)
            pm.base_color_map = mul
        )
        else pm.base_color_map = baseTex
    )

    v = d["roughness"]
    if v != undefined do pm.roughness = v

    v = d["roughmap"]
    if v != undefined do pm.roughness_map = BitmapTexture filename:(ct_findTex v)

    v = d["metalmap"]
    if v != undefined do
    (
        pm.metalness_map = BitmapTexture filename:(ct_findTex v)
        pm.metalness = 1.0
    )

    -- Opacity-karta (CoronaPhysical: 'opacitymap', CoronaMtl: 'transmap')
    v = d["opacitymap"]
    if v == undefined do v = d["transmap"]
    if v != undefined do
    (
        local opTex = BitmapTexture filename:(ct_findTex v)
        pm.cutout_map = opTex
        if ct_extraOpacitySlot != undefined do
        (
            try (setProperty pm ct_extraOpacitySlot opTex)
            catch (format "  OBS: kunde inte satta % pa Physical Material (kolla namnet med showProperties)\n" ct_extraOpacitySlot)
        )
    )

    -- Visa texturplatser som inte kunde tolkas
    try
    (
        for k in d.keys do
        (
            if (matchPattern k pattern:"slot*") and (d[k] != d["opacitymap"]) do
                format "  OBS: % har en oanvand texturplats % = %\n" mname k d[k]
        )
    )
    catch ()

    v = d["refraction"]
    if v != undefined do pm.trans_ior = v

    v = d["normalmap"]
    if v != undefined do
    (
        pm.bump_map = Normal_Bump normal_map:(BitmapTexture filename:(ct_findTex v))
        v = d["strength"]
        if v != undefined do pm.bump_map_amt = v
    )
    pm
)

-- Hamtar (eller skapar en gang) Physical-materialet for en materialnyckel ur coronaMtls.
-- Physical-materialet far Corona-materialets ursprungliga namn.
fn ct_getPhysical key =
(
    local pm = ct_physCache[key]
    if pm == undefined do
    (
        local d = coronaMtls[key]
        if d != undefined do
        (
            local mname = d["material"]
            if mname == undefined do mname = key
            pm = ct_buildPhysical mname d
            ct_physCache[key] = pm
            ct_created += 1
        )
    )
    pm
)

-- Returnerar materialet som ska sitta i stallet for m (m sjalv om inget byts).
-- Matchningen sker pa PLATS, inte namn: Max dopar om saknade material till
-- "Material #N", sa namnen fran filen gar inte att anvanda.
--   path  = "root" for objektets material, "root/2" for plats 2 i ett Multi/Sub
--   paths = objektets tabell ur coronaObjs (plats -> materialnyckel)
-- Multi/Sub-material behalls; deras saknade delmaterial byts pa plats.
fn ct_replaceMtl m path paths objName depth =
(
    local result = m
    if m != undefined and depth < 10 do
    (
        if ct_isMissing m then
        (
            local key = undefined
            if paths != undefined do key = paths[path]
            if key != undefined then
            (
                local pm = ct_getPhysical key
                if pm != undefined do result = pm
            )
            else
                format "  OBS: ingen Corona-data for '%' plats % ('%')\n" objName path m.name
        )
        else if (classOf m) == Multimaterial do
        (
            local n = m.materialList.count
            if paths != undefined and paths[path + "#n"] != undefined do
            (
                local pyCount = paths[path + "#n"] as integer
                if pyCount != n do
                    format "  OBS: '%' plats %: Multi/Sub har % platser i Max men % i filen, platserna kan vara forskjutna\n" objName path n pyCount
            )
            for i = 1 to n do
            (
                local sm = m.materialList[i]
                local nm = ct_replaceMtl sm (path + "/" + (i as string)) paths objName (depth + 1)
                if nm != sm do
                (
                    m.materialList[i] = nm
                    ct_subReplaced += 1
                )
            )
        )
    )
    result
)

-- Laser corona_data.ms och byter ut alla saknade Corona-material i scenen,
-- bade direkt pa objekt och inne i Multi/Sub-material.
-- Returnerar #(skapade Physical-material, objekt bytta direkt, bytta delmaterial).
fn ct_applyData dataFile =
(
    local direct = 0
    coronaMtls = undefined
    coronaObjs = undefined
    fileIn dataFile
    ct_created = 0
    ct_subReplaced = 0
    if coronaMtls == undefined or coronaObjs == undefined then
        format "OBS: ingen data lastes fran % (gammal corona_export.py?)\n" dataFile
    else
    (
        ct_searchDirs = ct_buildSearchDirs()
        ct_physCache = Dictionary #string

        -- undo av: annars haller Maxs undo-buffert kvar referenser till de gamla materialen
        undo off
        (
            for o in objects where o.material != undefined do
            (
                local m = o.material
                local nm = ct_replaceMtl m "root" coronaObjs[o.name] o.name 0
                if nm != m do
                (
                    o.material = nm
                    direct += 1
                )
            )
        )
        format "Klart: % Physical-material skapade, % objekt fick nytt material direkt, % delmaterial byttes i Multi/Sub\n" ct_created direct ct_subReplaced
    )
    #(ct_created, direct, ct_subReplaced)
)

-- ===== Rensa gamla (saknade) Corona-material =====

-- Ett "gammalt" material = ett saknat material, eller ett Multi/Sub med minst ett saknat delmaterial.
fn ct_isOldMtl m =
(
    local old = false
    if ct_isMissing m then old = true
    else if (classOf m) == Multimaterial then
    (
        for sm in m.materialList do
        (
            if ct_isMissing sm do old = true
        )
    )
    old
)

-- Rensar undo-buffert, tomer material-editorns slots fran gamla material som ingen anvander,
-- och skriver ut vilka gamla material som FORTFARANDE anvands av objekt (och av vilka objekt).
fn ct_cleanupOld =
(
    clearUndoBuffer()
    local oldMats = for m in scenematerials where (ct_isOldMtl m) collect m
    format "Gamla Corona-material i scenen: %\n" oldMats.count
    local stillUsed = 0
    local cleared = 0
    for m in oldMats do
    (
        local nodes = refs.dependentNodes m
        if nodes.count > 0 then
        (
            stillUsed += 1
            local n = nodes.count
            if n > 5 do n = 5
            local names = ""
            for i = 1 to n do names += nodes[i].name + "; "
            format "  ANVANDS FORTFARANDE: '%' (%) av % objekt: %\n" m.name (classOf m) nodes.count names
        )
        else
        (
            for i = 1 to meditMaterials.count do
            (
                if meditMaterials[i] == m do
                (
                    meditMaterials[i] = Standardmaterial()
                    cleared += 1
                )
            )
            local deps = for d in (refs.dependents m) where d != m collect ((classOf d) as string)
            format "  oanvant: '%' (%), refereras av: %\n" m.name (classOf m) deps
        )
    )
    gc()
    local after = for m in scenematerials where (ct_isOldMtl m) collect m
    format "Efter rensning: % gamla material kvar (% anvands av objekt, % rensades ur editor-slots)\n" after.count stillUsed cleared
    after.count
)

-- Hela flodet: spara/kolla scen, kor python, byt material.
fn ct_convert =
(
    local sceneFile = ct_prepareScene()
    if sceneFile != undefined then
    (
        local dataFile = ct_scriptDir + "corona_data.ms"
        local logFile = ct_scriptDir + "corona_export_log.txt"
        if doesFileExist dataFile do deleteFile dataFile
        format "Corona -> Physical: parsar %\n" sceneFile
        local code = ct_runPython ct_exportScript ("\"" + sceneFile + "\" \"" + dataFile + "\"") logFile
        ct_printFile logFile
        if code != 0 or not (doesFileExist dataFile) then
            messageBox ("Python-steget misslyckades (kod " + (code as string) + ").\nSe MAXScript Listener och " + logFile) title:"Corona -> Physical"
        else
        (
            local r = ct_applyData dataFile
            ct_cleanupOld()
            messageBox ((r[1] as string) + " Physical-material skapade.\n" + (r[2] as string) + " objekt fick nytt material direkt.\n" + (r[3] as string) + " delmaterial byttes i Multi/Sub.\nSe MAXScript Listener for detaljer.") title:"Corona -> Physical"
        )
    )
)

-- Diagnostik: skriver texturplatserna till dump.txt och oppnar den i Anteckningar.
fn ct_dump =
(
    local sceneFile = ct_prepareScene()
    if sceneFile != undefined then
    (
        local dumpFile = ct_scriptDir + "dump.txt"
        local code = ct_runPython ct_dumpScript ("\"" + sceneFile + "\" --slots") dumpFile
        if code != 0 then
            messageBox ("Python-steget misslyckades (kod " + (code as string) + "). Se " + dumpFile) title:"Corona -> Physical"
        ShellLaunch "notepad.exe" ("\"" + dumpFile + "\"")
    )
)

rollout ct_rollout "Corona -> Physical" width:250
(
    button btnConvert "Konvertera Corona -> Physical" width:230 height:34
    button btnClean "Rensa gamla Corona-material" width:230
    button btnDump "Diagnostik (dump.txt)" width:230
    button btnFolder "Oppna script-mappen" width:230

    on btnConvert pressed do ct_convert()
    on btnClean pressed do
    (
        local n = ct_cleanupOld()
        messageBox ((n as string) + " gamla material kvar.\nSe MAXScript Listener for detaljer.") title:"Rensa gamla material"
    )
    on btnDump pressed do ct_dump()
    on btnFolder pressed do ShellLaunch "explorer.exe" ct_scriptDir
)

fn ct_openDialog =
(
    try (destroyDialog ct_rollout) catch ()
    createDialog ct_rollout
)

-- Oppna dialogen nar filen kors (hoppas over om ct_skipDialog ar satt till true)
if ct_skipDialog != true do ct_openDialog()
