Steps: 

1. Get the webview for the comms up
    1.a. compose docker build
    1.b. compose docker comms

    Verify the webview its up and running:
    1.c curl https://127.0.0.1:8765/api/health ->port in constants, the ip need to fix it
    This runs the file msg_relay.run.py

2. Flash the firmware test - guide its in tools (FLASH TEST MODULE)
    2.a.stop the docker running comms
    2.b.run the devcontainer with espidf
    2.c.run tools/scripts/flash.sh select the firmware and port(verify the port with the webview)