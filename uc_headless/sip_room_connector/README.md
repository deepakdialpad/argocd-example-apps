# Build Dependencies
Docker file currently depend on these following git repos to be cloned in sip_room_connector/baresip
```
cd sip_room_connector/baresip
git clone https://github.com/creytiv/rem.git --branch v0.5.2
git clone git@github.com:dialpad/libre.git   
git clone git@github.com:dialpad/baresip.git
git clone https://github.com/traviscross/freeswitch.git
```

It also needs the certifcate files found at https://github.com/parlaylabs/fatline/tree/2.0/master/infra/docker/interop-agent
```
ca.crt
interopAgentWebSocketRootCA.pem
interopAgentWebSocket.key
interopAgentWebSocket.crt
interopAgentWebSocketRootCA.crt 
```
Copy them into the sip_room_connector folder. 

# Building the container image (locally)
From the uc_headless directory

First ensure you have extrenal repos,
```
sip_room_connector/baresip/clone_repos.sh
```
This clones `librem`, `libre`, and `baresip` into the sip_room_connector/baresip folder.

Build the base image,
```
docker build -t gcr.io/ucstaging/sip-room-agent-base:local -f sip_room_connector/sip_room_agent_base/Dockerfile .
```

Then build the full image,
```
docker build -t gcr.io/ucstaging/sip-room-agent:local --build-arg BASE_IMAGE=gcr.io/ucstaging/sip-room-agent-base:local -f sip_room_connector/sip_room_agent/Dockerfile .       
```

# Build the container image using GitHub Actions

[Build the SIP Room Agent Image](https://github.com/dialpad/firespotter/actions/workflows/dm-sip-room-agent.yaml)

1. Click `Run workflow`
2. Create release branch `false`
3. Patch manifest `false`
4. Environment `ucstaging`
4. Tag `<your_own_tag>`

[Build the SIP Room Agent Base Image](
https://github.com/dialpad/firespotter/actions/workflows/dm-sip-room-agent-base.yaml)

1. Click `Run workflow`
2. Select `branch`
3. Environment `ucstaging`
4. Tag `<your_own_tag>`

# Running (locally)

Ensure you have the v4l2 loopback device installed and configured on the host. If running locally:

```
sudo apt-get -y install v4l2loopback-dkms
sudo modprobe v4l2loopback devices=2 card_label=main,slides exclusive_caps=1,1
```

If running on cloud-dev, edit `/etc/modprobe.d/v4l2loopback.conf`:

```
options v4l2loopback devices=2 card_label=main,slides exclusive_caps=1,1
```

and then reload the module:

```
sudo rmmod v4l2loopback
sudo modprobe v4l2loopback
```

To run a local image:
```
docker run --rm -it --net=host --device=/dev/video0 --device=/dev/video1 --cap-add=SYS_PTRACE -e PUBLIC_NODE_IP=<ip address> -e MOSQUITTO_CONFIG_FILE=/etc/mosquitto/mosquitto.conf -e BARESIP_CONFIG_DIR=/root/.baresip -e VIDEODEV=0 -e CONTENTDEV=1 -e X_DISPLAY=:3 -e MQTTPORT=1883 -e MQTTWSPORT=2883 -e SIPPORT=5060 -e BFCPPORT=20500 -e RECORDER_AGENT_TYPE=sip-room-connector -e DEV_ACCOUNTS=true gcr.io/ucstaging/sip-room-agent:local
```
Substitute a bound IP address that your client device (or Linphone) will connect to. This could be a local or public IP.

To download the published image from Google Cloud Container Registry, configure access for Docker (https://cloud.google.com/container-registry/docs/advanced-authentication):
```
gcloud auth login
gcloud auth configure-docker
```

Pull the image from the registry (substituting other environment names for `ucstaging` if necessary):
```
docker pull gcr.io/ucstaging/sip-room-agent
```

Then run as above:
```
docker run --rm -it --net=host --device=/dev/video0 --device=/dev/video1 --cap-add=SYS_PTRACE -e PUBLIC_NODE_IP=<ip address> -e MOSQUITTO_CONFIG_FILE=/etc/mosquitto/mosquitto.conf -e BARESIP_CONFIG_DIR=/root/.baresip -e VIDEODEV=0 -e CONTENTDEV=1 -e X_DISPLAY=:3 -e MQTTPORT=1883 -e MQTTWSPORT=2883 -e SIPPORT=5060 -e BFCPPORT=20500 -e RECORDER_AGENT_TYPE=sip-room-connector -e DEV_ACCOUNTS=true gcr.io/ucstaging/sip-room-agent
```

To connect to Chrome, navigate to `chrome://inspect` and add a network target for `localhost:9000`. You can then open the inspector, navigate to Dialpad Meetings and join as normal. There should be a single WebRTC audio and video device representing the remote SIP client.

Note this Baresip configuration is not secure and is set to auto-answer any incoming call to `test`.

## Linphone
You can use linphone to connect the sip client (https://www.linphone.org/). Simply call `test@<your local ip>`.

# Running (kubernetes)

The cluster was created and managed using Terraform. For more information, take a look at https://github.com/dialpad/ucr-infra/tree/main/terraform/applications/sip_room_connector/ucstaging 

We run a DaemonSet to ensure that all nodes will run a copy of a pod. The pod that we are deploying helps initalize each nodes with a setup script that is in the specific node pool. In this case, it is the sip-room-connector node pool. The script installs the v4l2 loopback devices.

## Accessing GKE Cluster

```
# Get credentials to cluster
gcloud container clusters get-credentials sip-room-connector --region us-west2 --project ucstaging

# Verify the context
kubectl config get-contexts
```

## Configuring Cluster and Apply Manifests
This part should already be done and shouldn't need to be done again unless something was changed. 

```
# Apply taint on the node pool
kubectl taint nodes cloud.google.com/gke-nodepool=sip-room-connector:NoSchedule --selector cloud.google.com/gke-nodepool=sip-room-connector
```

```
# To remove taint on node pool
kubectl taint nodes cloud.google.com/gke-nodepool=sip-room-connector:NoSchedule- --selector cloud.google.com/gke-nodepool=sip-room-connector
```

```
# Apply the manifests

kubectl apply -f cm-entrypoint.yaml
kubectl apply -f daemonset.yaml
kubectl apply -f deployment.yaml
```

## Deploying Image To Your Node Pool

1. Create your own directory with your username in  sip_room_connector/apps/dev/overlays/your_username
2. Copy and modified sip_room_connector/apps/overlays/dev/bpham/deployment.yaml
2. I would just replace anything with bpham with your own username and make sure node selector is pointed to your node pool name that was created
3. CD into sip_room_connector/apps/overlays/dev/your_name/deployment.yaml
2. `kubectl apply -f deployment.yaml`
3. `kubectl get pods` to verify your pod was deployed

## Verify baresip/chrome stack
```
# Get the name of the pulse-test pod
kubectl get pods

# Use port forwarding to access the application
kubectl port-forward <pod name> 9000:9000

# Access chrome in local browser
localhost:9000

# Start dialpad meeting
Start a dialpad meeting locally and join that meeting on the chrome client

# Get node public ip
kubectl get nodes -o wide

# Connect to sip client on linphone
test@<node public ip>
```

# End-to-end development workflow

You can create an end-to-end deployment in `ucstaging` for development and testing purposes:

1. Create your own Pub/Sub topic and subscription:
```
gcloud pubsub topics create sip-room-connector-requests-<personal identifier> --project ucstaging
gcloud pubsub subscriptions create --topic=<topic name from above> --ack-deadline=10s --message-retention-duration=600s --project ucstaging
```
2. In `firespotter/uberconf/constants.py`, set the following:
```
# publish room connector requests to your personal topic
SIP_ROOM_CONNECTOR_REQUEST_PUBSUB_TOPIC = 'projects/%s/topics/<topic name from above>' % app_name
# required to use your custom version for the recorder URL in room connector requests  
CUSTOM_STAGING_DOMAIN='https://<your uberconf GAE version name>-dot-ucstaging.appspot.com'
ECG_AUTH_SECRET='X'
```
3. Next, deploy your custom app engine version (refer to `uberconf` README).
4. [Follow the steps here](apps/overlays/dev) to create a personal deployment of a SIP agent that listens to the
subscription above.

Currently, the ECGs are only configured for the main app engine versions on each environment and the version cannot be
overridden at runtime through e.g. the SIP URI or headers. As a workaround to test the E2E room
connector flow (minus the ECG), you can send the initial `api/i1/siproomconnector` request with curl. For example:

`curl -X POST https://<your version name>-dot-ucstaging.appspot.com/api/i1/siproomconnector -H 'X-Ecg-Secretkey: X' -d '{"sip_uri": <your SIP URI string>, "display_name": <your display name>}'`

If everything is working correctly, this will return a 200 response and publish a Pub/Sub request that should be pulled
by your personal dev Pod.

To use your SIP device, just dial the internal SIP URI from the API response.

# Deploying SIP Agents

The SIP agent deployment for a given project should generally be done AFTER the app engine deployment has
completed; however, in exceptional situations the agents can be deployed first if it's easier to preserve
compatibility with the `uberconf` service by doing so.

The release process does not introduce downtime for the agents. Any SIP agents that are actively serving a
room connector session when the rollout is initiated will continue processing the session until its complete.
When a given session does complete, the agent will terminate and then be replaced with a new replica.

The SIP agent images are versioned and tagged by the `git describe` output from the release branch commit they are built
from.

## Manual SIP agent deployment

**Note: this is an interim and abbreviated manual process to deploy the SIP agents. This will be deprecated after the
automated GHA workflows are fully tested.**

Make sure you have the [kustomize tool installed](https://kubectl.docs.kubernetes.io/installation/kustomize/) before
proceeding.

Follow the steps below to update the SIP agents:

1. `git fetch && git checkout uc_live`
2. `SIP_AGENT_TAG=$(git describe --long --exclude 'ucr/*')`
3. Either a) [build new images](#building-and-publishing-new-images) or
b) [promote them from another environment](#promoting-images)
4. `git checkout master`
5. Update the container patch file for the appropriate environment
`uc_headless/apps/overlays/<env>/patch_containers.yaml` with the new image tags. For example, if `SIP_AGENT_TAG` is set
to `DPM2021-12-15-445-gd5cb2922d31` for a prod deployment, the session controller entry should look like this:
```
...
spec:
  template:
    spec:
      containers:
      # replace the old session controller image tag with the new DPM2021-12-15-445-gd5cb2922d31 tag here
      - image: gcr.io/uberconf/sip-room-session-controller:DPM2021-12-15-445-gd5cb2922d31
...
```

Make sure to replace the tag like this for any new images you wish to deploy in this release.

6. Replace the Datadog version tag label value in the `uc_headless/apps/overlays/<env>/add_labels.yaml` patch file
for the environment. For example, for the `DPM2021-12-15-445-gd5cb2922d31` release, the entry should look like this:

```
labels:
  tags.datadoghq.com/version: "DPM2021-12-15-445-gd5cb2922d31"
```
7. Commit and merge the changes to the patch files to master. Copy the Git commit hash.
8. `git checkout uc_live`
9. `git cherry-pick <commit hash from step 6>`
10. `git push origin uc_live`
11. Deploy the SIP agent k8s resources to the cluster as described here.

### Building and publishing new images

First, update the base image for the room agent container if needed (i.e. if there are changes to baresip or other
dependencies for the room agent image):

1. Navigate to https://github.com/dialpad/firespotter/actions/workflows/dm-sip-room-agent-base.yaml
2. Under "Environment", enter the appropriate GCP project id
3. For "Tag", enter a value or just use the default
4. Run the workflow and wait for it to complete.

Next, update the `sip-room-agent` and `sip-room-session-controller` images. Run the commands below from your dev
environment.
```
cd <path to firespotter/uc_headless>

# Set an env variable for the GCP project id (ucstaging, uc-beta, or uberconf) 
GCP_PROJECT_ID=<project id>

# Set an env variable for the base image name from above
SIP_ROOM_AGENT_BASE_IMAGE=gcr.io/$GCP_PROJECT_ID/sip-room-agent-base:<tag from above>

# build the session controller image
docker build -t gcr.io/$GCP_PROJECT_ID/sip-room-session-controller:$SIP_AGENT_TAG -f sip_room_connector/session_controller/Dockerfile .

# push to GCR
docker push gcr.io/$GCP_PROJECT_ID/sip-room-session-controller:$SIP_AGENT_TAG

# build the SIP room agent image
docker build --build-arg BASE_IMAGE=$SIP_ROOM_AGENT_BASE_IMAGE -t gcr.io/$GCP_PROJECT_ID/sip-room-agent:$SIP_AGENT_TAG -f sip_room_connector/sip_room_agent/Dockerfile .

# push to GCR
docker push gcr.io/$GCP_PROJECT_ID/sip-room-agent:$SIP_AGENT_TAG
```

### Promoting images

Instead of building new images from scratch, it can be useful to "promote" images from one environment to another,
in order to e.g. publish the current uc-beta images to prod. To do this, just tag the original image with the new tag
and push it to the repository.

For example, here are the steps to promote the beta `sip-room-agent` and `sip-room-session-controller` images to prod:
```
docker tag gcr.io/uc-beta/sip-room-session-controller:<version tag> gcr.io/uberconf/sip-room-session-controller:<version tag>
docker push gcr.io/uberconf/sip-room-session-controller:<version tag>

docker tag gcr.io/uc-beta/sip-room-agent:<version tag> gcr.io/uberconf/sip-room-agent:<version tag>
docker push gcr.io/uberconf/sip-room-agent:<version tag>
```

### Deploying kubernetes resources

From the release branch on your local dev environment:

1. First, make sure kubectl is configured for cluster you wish to deploy to:
`gcloud container clusters get-credentials sip-room-connector --region=us-west2 --project=<project id>`
2. Navigate to the `firespotter/uc_headless` directory
3. Run `kustomize build sip_room_connector/apps/overlays/<env>`. This will resolve environment-specific patches to the
base configuration and print out a concatenated list of k8s resource definitions separated by dashes.

Inspect the output to validate things look as expected:
- In the list, find the sip-room-agent Deployment and verify the container image tags are correct
- Ensure the resources have the new `tags.datadoghq.com/version` that was updated in the labels patch file

4. Now, apply the resources to the cluster to deploy them: `kustomize build sip_room_connector/apps/overlays/<env> | kubectl apply -f -`
5. Verify the deployment is healthy: run `kubectl get pods -n sip-room-connector` a few times while waiting for the 
replicas to finish updating. For Pods that are still serving a session, the output will show 'Terminating' in the status
column. Once a given Pod finishes it session, its containers will exit; k8s will then remove it automatically and
replace it with a new, updated replica. Outside of these active Pods, the other agents should be cleaned up quickly and
then replaced with new Pods. Within a few minutes, the new replicas should have the `Running` status.

It's also usually a good idea to check the logs of a running pod to make sure it started normally.

Find any running pod with `kubectl get pods -n sip-room-connector` and then run:
`kubectl logs -f -n sip-room-connector <pod name> -c session-controller`
to tail the logs for the `session-controller` container. To do the same for the `sip-room-agent` container:
`kubectl logs -f -n sip-room-connector <pod name> -c sip-room-agent`

If something went wrong such that the containers are failing and restarting immediately, you may have to view the logs
for the deployment from the GKE console. Navigate to https://console.cloud.google.com/kubernetes/list/overview, select
the cluster and then clock on the SIP room connector deployment. Then click "Container logs" to view the logs for all
pods managed by the deployment. 
