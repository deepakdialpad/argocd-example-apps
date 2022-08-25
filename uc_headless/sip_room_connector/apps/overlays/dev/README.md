# SIP Room Connector Agent Dev Workflow
The `bill` folder in this directory defines an example dev configuration for the SIP room connector agent.
This relies on Kustomize, the Kubernetes configuration tool 
([installation instructions here](https://kubectl.docs.kubernetes.io/installation/kustomize/)).

Using the `bill` example as a template, create your own configuration to override values of the base Deployment and
ConfigMap definitions.

Then, apply the resulting resources to the cluster to run your own dedicated instance of the agent.

Refer to the instructions below for details.

## Usage

 1) Choose a personal identifier (make sure to use one that is not already in use) to replace instances of `bill` in the
 example and set this environment variable:

    ```
    MY_ID=<your personal name or id>
    ```
 
 2) If desired, first make your own modifications to the room connector agent images (code changes etc.), and then,
    build and push the images:
    ```
    # session controller
    docker build -t gcr.io/ucstaging/sip-room-agent:$MY_ID -f sip_room_connector/sip_room_agent/Dockerfile .
    docker push gcr.io/ucstaging/sip-room-agent:$MY_ID

    # sip room agent
    docker build -t gcr.io/ucstaging/sip-room-agent:$MY_ID -f sip_room_connector/sip_room_agent/Dockerfile .
    docker push gcr.io/ucstaging/sip-room-agent:$MY_ID
    ```
 
    Optionally, you may want to use your own SIP room agent base image. If so, for the room agent, follow the
    instructions below instead. This is only required if you have changes to baresip or other dependencies for the
    room agent image.

    ```
    # sip room agent base
    docker build -t gcr.io/ucstaging/sip-room-agent-base:$MY_ID -f sip_room_connector/sip_room_agent_base/Dockerfile .
    docker push gcr.io/ucstaging/sip-room-agent-base:$MY_ID
    
    # sip room agent with custom base
    docker build --build-arg BASE_IMAGE=gcr.io/ucstaging/sip-room-agent-base:$MY_ID -t gcr.io/ucstaging/sip-room-agent:$MY_ID -f sip_room_connector/sip_room_agent/Dockerfile .
    docker push gcr.io/ucstaging/sip-room-agent-base:$MY_ID
    ```
    
    Alternatively, if you don't need to test custom changes to the images, you can just tag existing images (such as
    images from prod or beta):
    ```
    docker tag <source image> gcr.io/ucstaging/<image name>:$MY_ID
    docker push gcr.io/ucstaging/<image name>:$MY_ID
    # repeat for all of the images 
    ```

 3) Create a new directory `overlays/dev/$MY_ID` for your dev Kustomization. Make modified copies of the files
 in this  directory, replacing instances of `bill` in the text of the config files with `$MY_ID`.


 4) Verify your configuration:
    ```
    kustomize build /path/to/overlays/dev/$MY_ID
    ```
    
    You should see modified ConfigMap and Deployment resources:
    * Both resource names should be updated to include a `%MY_ID-` prefix, and all other references to those
    resources should reflect the new values as well
    * All of the label values should be prefixed with `$MY_ID`
    * All of the image names in the Pod template should be tagged as `$MY_ID`

 5) Deploy the modified resources to the cluster:
    ```
    kustomize build '/path/to/overlays/dev/$MY_ID' | kubectl apply -f -
    ```

 6) Verify the expected resources have been created on the cluster:
     ```
     # connect the VPN and configure kubectl with the dev cluster credentials 
     kubectl get configmaps -n sip-room-connector
     kubectl get deployments -n sip-room-connector
     kubectl get pods -n sip-room-connector
     ```
    
     In the output for each of the above commands, you should see a Configmap/Deployment/Pod resource listed, prefixed
     with`$MY_ID`.

Your Pod should start running shortly after. Check the logs to make sure they look as expected. For instructions on this
and general troubleshooting commands, refer to [Working with SIP agents](#working-with-sip-agents).

Once the initial setup is done, you can simply rebuild and push your image(s) whenever you have code changes you want
to deploy. This should only take a few seconds since most image layers will still be cached. Then, restart your
Deployment to create a new replica that uses the updated images:
`kubectl rollout restart deployment -n sip-room-connector
<your deployment name>`. Note, if your Pod is actively serving a room connector session, you must end the
session somehow (hangup, stop the call, etc.) before your Pod will terminate.

Furthermore, if you need to deploy additional changes to the Deployment/ConfigMap definitions, just deploy them in the
same way as in step 5). You may still need to restart your Pod, e.g. if you only deploy ConfigMap changes.

## Working with SIP agents

Below are some useful commands for working with your deployment:

List pods in the `sip-room-connector namespace` (your own dev pod should appear in this list): with
`kubectl get pods -n sip-room-connector`

Describe pod: `kubectl describe pod -n sip-room-connector <pod name>`

Describe deployment: `kubectl describe deployment -n sip-room-connector <deployment name>`

Tail logs for a container: `kubectl logs -f -n sip-room-connector <pod name> -c <container name>` where the container
name is one of `sip-room-agent` or `sip-room-session-controller`)

Exec into containers and run commands, e.g. to use a bash shell with the `sip-room-agent` container:
`kubectl exec -it -n sip-room-connector <pod name> -c sip-room-agent -- bash`

Port-forward the CDP port of the chrome instance your agent runs: `kubectl port-forward -n sip-room-connector <pod name> 9000:9000`
(visit localhost:9000 in your browser to attach)

Force kill a pod (use this only as an occasional last resort if your Pod won't terminate normally for some reason):
`kubectl delete pod -n sip-room-connector <pod name> --force`

If your Pod is restarting in a loop, navigate to the GKE console, find your deployment, and click "Container Logs" to
troubleshoot.

The following links also have some useful troubleshooting tips:
https://kubernetes.io/docs/tasks/debug-application-cluster/debug-application/
https://kubernetes.io/docs/tasks/debug-application-cluster/debug-running-pod/
https://kubernetes.io/docs/tasks/debug-application-cluster/debug-init-containers/
