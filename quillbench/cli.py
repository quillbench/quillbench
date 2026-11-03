"""
``quillbench`` -- command-line tool for QuillBench.

    quillbench serve                         run the web app (port and path from settings)
    quillbench validate [WORKSPACE]          check every registered workspace (or one)
    quillbench workspace list|add|remove     the registry of paper workspaces
    quillbench init-workspace FOLDER         start a new paper workspace from a template
    quillbench user list|add|passwd|role|disable|enable
    quillbench assign USER WORKSPACE BATCH...  [--remove]
    quillbench status WORKSPACE              progress per annotator and batch
    quillbench quality WORKSPACE [PROJECT]   agreement, check items, time per item
    quillbench export WORKSPACE [--project P] [--real-ids] [--no-notes]
    quillbench backup                        archive answers + studio state now
