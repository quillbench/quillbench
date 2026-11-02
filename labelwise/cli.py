"""
``labelwise`` -- command-line tool for Labelwise.

    labelwise serve                         run the web app (port and path from settings)
    labelwise validate [WORKSPACE]          check every registered workspace (or one)
    labelwise workspace list|add|remove     the registry of paper workspaces
    labelwise init-workspace FOLDER         start a new paper workspace from a template
    labelwise user list|add|passwd|role|disable|enable
    labelwise assign USER WORKSPACE BATCH...  [--remove]
    labelwise status WORKSPACE              progress per annotator and batch
    labelwise quality WORKSPACE [PROJECT]   agreement, check items, time per item
    labelwise export WORKSPACE [--project P] [--real-ids] [--no-notes]
    labelwise backup                        archive answers + studio state now
