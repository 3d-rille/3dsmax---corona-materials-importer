First release of a MaxScript/Python tool that converts Corona materials to Physical Materials. This script reads the material data from the file, allowing you to swap materials without having the Corona renderer installed locally. Tested in Max


corona_dump.py

corona_export.py	
This is a file Baserad on Blender-importern io_scene_max project by:
2023-2026 Sebastian Schrand
2017-2022 Jens M. Plonka  (ImportMAX)
2005-2018 Philippe Lagadec (olefile)

corona_replace.ms

corona_tool_run_this_in_max.ms    <---  run this file in max, 
(You have to set the path to the python-files in this file to) 



----------------corona_tool_run_this_in_max.ms---------------
-- ===== INSTALLNINGAR) =====  (Settings in the scriptfile )
global ct_scriptDir = @"A:\script\max\aaaa_RN\dev_rn\corona_material_import\"   -- This have to be the folder that the phytonfiles live in.
-------------
global ct_python = "python"                    -- eller full sokvag, t.ex. @"C:\Python312\python.exe"
.........................
.........................
.........................




# corona_export.py
# Laser Corona-material ur en .max-fil (utan Corona, Max eller Blender) och
# skriver en MAXScript-fil (.ms) med materialdatan.
#
# Baserad pa Blender-importern io_scene_max:
#   Copyright 2023-2026 Sebastian Schrand
#             2017-2022 Jens M. Plonka  (ImportMAX)
#             2005-2018 Philippe Lagadec (olefile)
# Licens: GPL-2.0-or-later
