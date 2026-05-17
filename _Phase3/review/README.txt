LAMAHAT — Phase 3 review dossier
=================================

This directory is the contract between the prebuild pass (which fetches and scores candidate images) and the render pass (which burns the video).  Edit it freely between the two passes.

WHAT'S IN HERE
--------------
  **decisions.json**       The file you edit.  Hand-edit-friendly JSON.
  **overrides**/           Drop your own .jpg / .png files here.
  **shot_NN_VISUAL**/      One folder per image-needing shot.  Contains:
                       - **context.txt**     what this shot is about
                       - **candidates.json** same as decisions.json["shots"]["NN"]["candidates"]
                       - **SOURCE_X.jpg**    the actual downloaded candidate

WHAT YOU CAN CHANGE IN decisions.json
-------------------------------------
Per shot:
  * "chosen"        Move to a different candidate by copying its
                    "source:title" string here.
  * "override"      Set to a file path under this directory (typically
                    "overrides/shot_NN.jpg") to use your own image.
                    Overrides win over everything else.

Global:
  * "pinned_portrait"  Set to a path under overrides/ (e.g.
                       "overrides/character.jpg") to use one canonical
                       portrait at every "portrait" shot.

EXAMPLES
--------
Use my own picture for shot 3:
  1. Save your image as overrides/shot_03.jpg
  2. In decisions.json, find shot "3" and set:
        "override": "overrides/shot_03.jpg"

Use the same Jafar al-Askari portrait at every "portrait" shot:
  1. Save the photo as overrides/character.jpg
  2. In decisions.json, set:
        "pinned_portrait": "overrides/character.jpg"

Swap to the Wikimedia candidate instead of the Pexels one:
  1. Open shot_NN_portrait/candidates.json
  2. Copy the "source:title" of the candidate you prefer
  3. In decisions.json, paste it into "chosen"

THEN
----
  python render_plan.py --plan ... --review-dir <this-dir> --output ...
