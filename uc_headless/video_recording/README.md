# Video Recording

# Release process

## Overview

The release process involves building and deploying the new docker images, as well as deploying all of the Kubernetes
manifests for the recorder agent application.

The process is fully automated through a GitHub Actions job, although after running the action and letting the full
deployment process take its course, there should also be some basic verification that the process completed
successfully (see the list of steps in the [next section](#deploying-production)).

More specifically, this is the sequence of events for the recorder agent release:

The GitHub Actions (recorder-push) job is used to deploy the recorder agent for the desired environment. 

This action does the following:

*   Checks out the configured release branch (`uc_live` for prod), and then builds, tags, and pushes the new recorder agent
    images. The docker image tags are the output of `git describe --long`. The images are then pushed to GCR under the
    appropriate UC project.
    
*   Checks out `master` and generates a new patch file that overrides the image tags for that environment.
    The change is committed to `master` and the branch is pushed. 
         
    For example, for `uberconf`, the current recorder agent image tags are indicated [here](https://github.com/dialpad/firespotter/blob/master/uc_headless/video_recording/apps/overlays/uberconf/patch_containers.yaml).
    
*   Checks out the release branch for the environment again, and cherry picks the new commit from master to it.
    Pushes the branch.

*   Finally, a git tag is updated to refer to the new commit in the branch. 

From here, the cluster continuous deployment (CD) tool [Flux](https://toolkit.fluxcd.io/) reconciles the cluster with the newest
changes since the last time the tag was updated on the release branch.

Flux constantly polls `firespotter` on an interval (currently, every 60 minutes to prevent high mem-usage because of the firespotter repo size) for the current commit at the head
of the configured release branch that the tag refers to. During releases, we usually force reconcile the change. 

For `uberconf`, the branch is `uc_live` and the tag is named `ucr-prod`.

Once the tag is updated, the next time Flux polls `firespotter` for the newest commit at that tag, then pull the commit,
detect any changes to the Kubernetes manifests, and then apply them in the specified order.

The base manifests can be found here:
https://github.com/dialpad/firespotter/tree/master/uc_headless/video_recording/apps/base.

Here are the environment-specific override patches for `uberconf`:
https://github.com/dialpad/firespotter/tree/master/uc_headless/video_recording/apps/overlays/uberconf.

The patch manifests for `uberconf` are applied in the order specified here:
https://github.com/dialpad/firespotter/blob/master/uc_headless/video_recording/apps/overlays/uberconf/kustomization.yaml

Flux resolves the final manifests by applying the per-environment patches to the base manifests to produce new
manifests, and applies those to the cluster with Kubernetes API requests, just like a human user might using
`kubectl apply` (or `kubectl delete` for manifests that were removed).

The final manifests are applied in the following order:
https://github.com/dialpad/firespotter/blob/master/uc_headless/video_recording/apps/base/kustomization.yaml

So, since the previous release, at a minimum, there will be new Docker images for the Deployment object. Any other
changes to the Kubernetes manifests will be applied as well.

## Deploying production

1.   Wait for the `uberconf` UC deployment (UC client and service) to complete 

2.   Navigate to https://github.com/dialpad/firespotter and select the "Actions" tab

3.   Under the `Workflows` section on the left, click on `Recorder Push`
     
4.   Click on `Run workflow` dropdown that appears on the right. Use the following settings:
     
     * **Use workflow from**: `uc_live`
     * **Update prod manifests**: `true`
     * **Environment**: `uberconf`
     * **Default to latest tag**: `latest`
    
5.   Click **Run Workflow**

6.   Once the job is completed. It will take Flux about 60 minutues to pick up the new changes. We can either wait, or   force reconcile by running `flux reconcile source git firespotter`.
7.   
     Verify the entire deployment completed successfully by viewing the
     [Live Health Summary Datadog dashboard](https://app.datadoghq.com/dashboard/9df-wd9-fzm/ucr-live-health-summary).

     You should see the following on the graph `Desired vs. Updated vs. Available Recorder Agents`:
     * The "available" number of recorder agents should have briefly dipped to 0 or nearly 0 sometime after the release
       started.
     * `desired_replicas` should equal `updated_replicas` (a large value in the 100s for `uberconf`)
     * There should also be no errors shown on the dashboard since the point at which the release started.
    
     If this looks as described, the process is complete. If not, the release may still be completing or an issue may
     have occurred. Refer to section below for more information.

## Troubleshooting and additional optional verifications steps

- Debugging Flux
  - Verify that Flux has picked up the latest commit tag that was created. You can do this by running `flux get sources git` and comparing the tags / commit. Did you reconcile already? If you did and Flux didn't pick it up, there might be a bad manifest. Take a look at the source-controller logs using kubectl to see if anything stands out.
  - If the `sources/git` looks good, you might want to take a look at the kustomizations to see if it got applied properly to the cluster. You can do this by running `flux get kustomizations` to see if there are any errors. Again, compare the git tags to verify they are updated. 

- Optional Verification Steps
  - Start a recording on UberConference and verify that the recording works.

# Recorder Development

All video recording changes should be tested using the `dev-ucr-cluster` Google Kubernetes Engine (GKE) cluster in the 
`ucstaging` project. 

**Even if the changes are limited to the UC
client or service**. **Please DO NOT use `uc-beta`, as QA and other internal users rely on beta.**

## Development Workflow

Currently, changes should be tested directly against the `dev-ucr-cluster` cluster in the `ucstaging` project. Since the cluster is shared by many users, you need to replicate some of relevant infrastructure resources in the `ucstaging` project
for your own use, so that the effects of your changes are decoupled from other users and the rest of the applications 
running on the cluster as much as possible.

### Infrastructure Setup

#### gcloud, kustomize, kubectl setup

If necessary, first install and set up `gcloud`, `kustomize`,  `kubectl`; see [Prerequisites](#prerequisites) for instructions.

Configure `kubectl` with credentials for the `ucstaging` cluster:

`gcloud container clusters get-credentials gke_ucstaging_us-west1_dev-ucr-cluster --project ucstaging`

#### Pub/Sub

Create your own subscription in `ucstaging` using your name as a prefix: 

1)   `gcloud pubsub subscriptions create <name>-recorder-agent-subscription --project ucstaging 
     --topic <name>-recording-requests --ack-deadline 30`
     
#### Docker Images

For more information, see [Docker Images and Registry](#docker-images-and-registry).

### Deploying the application

#### Configure and deploy UC

First, make the following changes and deploy your UC version to app engine:

1)   Edit the contents of `VIDEO_RECORDING_REQUEST_PUBSUB_TOPIC` in the file `uberconf/constants.py`
     to refer to the Pub/Sub topic you created above, e.g.
     
     `VIDEO_RECORDING_REQUEST_PUBSUB_TOPIC = '<name>-recording-requests'`
     
2)   In the same file, edit your to refer to the UC URL for your app engine version, e.g.
     
     `CUSTOM_STAGING_DOMAIN = '<version>-dot-ucstaging.appspot.com'`

3)   Deploy your UC app engine version. See the push script details in the 
     [UC README](../../uberconf/README.md) for instructions.
     
     
#### Deploy the recorder agent Pod(s) to the cluster
     
1. Create a directory and make a copy of the up-to-date configmap and deployment manifests. Run the following commands in `firespotter/uc_headless/video_recording`

    `$ mkdir apps/overlays/dev/<username>` 
    
    `$ cp apps/overlays/dev/bpham/* apps/overlays/dev/<username> ` 

    `$ cp apps/base/04-configmap.yaml apps/overlays/dev/<username>/configmap.yaml && cp apps/base/05-deployment.yaml apps/overlays/dev/<username>/deployment.yaml && cd apps/overlays/dev/<username>`

1. Update the following manifests with your namePrefix and pubsub subscription.

    - `PUBSUB_SUBSCRIPTION_ID` in `patch_dev.yaml`

    - Add the following config to` spec.template.spec` in `patch_dev.yaml` so that recorder will properly land on your node pool
    ```
    tolerations:
      - key: cloud.dialpad.com/gke-nodepool
        effect: "NoSchedule"
        operator: "Equal"
        value: <nodepool-name>
    nodeSelector:
      cloud.dialpad.com/gke-nodepool: <nodepool-name>
    ```
    
    - `namePrefix` in `kustomization.yaml` to `<username>-`

1. Build and apply your manifests to the cluster.

    `kustomize build . | kubectl apply -f -`
     
1. Verify that your Pod(s) have the Pod status `Running` (the startup may take a few seconds):
     
     `kubectl get pods -n recorder`
     
     There should be a pod name(s) prefixed with your Deployment name listed in the output:
     `<name>-recorder-agent-*`
     
3.   When you're finished with the Pod(s), or need new changes to your Docker images or ConfigMap etc. to take effect,
     delete your deployment:
     
     `kustomize build . | kubectl delete -f -`
     
     Then (if applicable) apply it again (step 1.)

#### Inspecting Pods

* List all user-created/managed Pods: `kubectl get pods -n recorder`
* Get detailed info about a Pod and its current state: `kubectl describe pod <pod name> -n recorder`  
* Exec into a container with a bash shell: `kubectl exec -it <pod name> -c <container name> -- /bin/bash` 
* Stream container logs: `kubectl logs -f <pod name> -c <container name> -n recorder`
  
The container name in the `-c <container name>` options above will be one of `session-controller` or `browser-agent`
(these names are defined in the Deployment manifest).  
  
See the K8s docs on `kubectl` for more.

#### Troubleshooting

Sometimes when there's a problem with the cluster, recording requests build up in the Pub/Sub topic and become invalid.
Once the issue is fixed, Pods will attempt to serve any requests that are still queued.

The fastest way to clear all the queued messages is to recreate the Pub/Sub subscription:

1)    Delete the subscription:

      `gcloud pubsub subscriptions delete <recorder agent subscription> --project <GCP project ID>`

2)    Create the subscription again – see [Pub/Sub](#pubsub)

## Flux Development Workflow

While it is nice to get started quickly, this is not how we deploy to our other environments. For that, we take advantage of Flux. A GitOps tool for keeping Kubernetes clusters in sync with sources of configuration and automating updates to configuration when there is new code to deploy. 

To learn more about this or use Flux to deploying to dev cluster, take a look at [Flux Development Workflow in our ucr-infra repository](https://github.com/dialpad/ucr-infra/#developer-workflow).

# Debugging

## Debugging the recorder client

You can forward the CDP port to use DevTools on the remote Chrome instance running within a Pod.

1.   Find and copy the name of the Pod of interest from the output of `kubectl get pods` or another source.

2.   Forward the CDP port with `kubectl port-forward <CDP PORT>:<CDP PORT>` (refer to the cluster ConfigMap for the 
     port value)
     
3.   Open a new tab in your local Chrome application and navigate to `localhost:<CDP PORT>`. From this page, you can 
     view/attach to open tabs and utilize DevTools as you normally would in your local Chrome application.

# Logs and monitoring

## Viewing logs in the GCP console

1)   Navigate to https://console.cloud.google.com/logs/viewer

2)   From the top menu, select the appropriate GCP project where the recorder agent cluster of interest
     is hosted.

3)   Select "Query Builder" and enter the following:

     ```
     resource.type="k8s_container"
     resource.labels.project_id="<project ID>"
     resource.labels.cluster_name="<cluster name>"
     ```

     For more granular views, you can also filter in conditions like `resource.labels.pod_name="<pod name>"`,
     `resource.labels.container_name="<container name>"` (refer to the workload manifest for the container names),
     and so on.

4)   Hit "Run Query" 

## GKE dashboard

GKE has a wealth of tools for inspecting the state of the cluster, its workloads, etc.

Visit https://console.cloud.google.com/kubernetes/list (select the GCP project if needed).

## Datadog

### Dashboards

There are two Datadog dashboards to view the health of the recorder agents, clusters, and Pub/Sub subscriptions:

1. [Live Health Summary](https://app.datadoghq.com/dashboard/9df-wd9-fzm/ucr-live-health-summary?from_ts=1618608587928&to_ts=1618612187928&live=true) - This dashboard is meant to summarize the
   most important health metrics. Useful for viewing the current health at a glance.
2. [UCR Metrics](https://app.datadoghq.com/dashboard/mz2-s4h-ux2/ucr-metrics?from_ts=1618525822031&live=true&to_ts=1618612222031) - This dashboard provides a much more granular
   view that's useful for debugging specific issues and viewing data over time. 

At the top of the dashboards, there is a dropdown for each dashboard "template variable". You should see template 
variables for project id, cluster, etc. in all of the dashboards listed above. The defaults are set for `uberconf`
(prod). If you are interested in a different environment, select the appropriate values.

### Monitors

We have monitors on a few of the most important health metrics for the video recording service, which will send Datadog
alerts under certain conditions.

1. [Recorder agent errors](https://app.datadoghq.com/monitors/31173038) - tracks the count of all recorder agent errors.
   Graphs of the specific error types can be seen in the in-depth dashboard listed above. 
2. [Percentage of available recorder agents](https://app.datadoghq.com/monitors/31178144) - monitors the percentage of
   recorder agents available to accept a new recording request.
3. [Oldest unacknowledged message age](https://app.datadoghq.com/monitors/31176712) - tracks the age of the oldest
   recording request in the Pub/Sub subscription that has not been acknowledged yet. Recording requests should/need to
   be accepted quickly by recorder agents; if this goes above a few seconds, it's likely due to a lack of available
   recorder agents are available for whatever reason (recorder agent failures, lack of cluster resources/pods, etc.) or
   Pub/Sub errors. User recording requests are likely timing out and failing.
   
The monitors above track metrics and generate alerts independently for each environment.

# Recorder Infrastructure (Deprecated)

We originally started off creating all the infrastructure manually, but decided to automate the process as well. The steps below are still valid, but most likely outdated. The better approach if you wanted to spin up infrastruture would be to use Terraform which can create the entire stack in a few mins. For a more up-to-date guide of how we deploy infrastructure, take a look at our [terraform directory](https://github.com/dialpad/ucr-infra/tree/main/terraform) in our [ucr-infra](https://github.com/dialpad/ucr-infra) repo. 

## Setup Guide

The sections below describe how to create the current infrastructure from scratch in a new GCP project.

Most of the instructions below refer to `gcloud` commands, but these steps should be doable from both `gcloud` and
the GCP console.

### Prerequisites

**Set up the cloud SDK:**

1.   [Optional] Install an up-to-date version of cloud SDK. Currently, the cloud SDK version used to work with app 
     engine is 274, but some of the `gcloud` commands in this README will require a more recent version. If you are not 
     setting up a new cluster, an older SDK is probably fine.
     
     Some options:
     *   Maintain both a legacy version and an update-to-date SDK on your system by installing the following Docker
         image for one version:
         https://hub.docker.com/r/google/cloud-sdk/
         
         Follow the setup instructions at the link.
         
         To run commands, you may find it useful to use a alias for the docker run command specified in the link.
         
         Bash on MacOS: 
         `echo 'alias cloudsdk="docker run --rm -ti --volumes-from gcloud-config google/cloud-sdk"' >> ~/.bash_profile`
         
         Then, you can run commands using the SDK installed with image like so: `cloudsdk gcloud ...`
     
2.   If necessary, authenticate with gcloud via Google OAuth: `gloud auth`.

**Install `kubectl`:**

Follow the instructions at https://kubernetes.io/docs/tasks/tools/install-kubectl/. Per the compatibility
policy for `kubectl`, you should use a `kubectl` version that's within +/- 1 version of the cluster.

Check the cluster version as follows: `gcloud container clusters describe <cluster name> --project <GCP project ID>`

**Tips:**

* You can omit the --project, --zone, and --region flags from `gcloud` commands if you set those properties as defaults 
in your config:

  `gcloud config set project <project ID>`

  `gcloud config set compute/region <region>`

  `gcloud config set compute/region <zone>`
  
  To override a given default config, just pass the corresponding flag in the gcloud command.
  
### GCP IAM service account

The recorder application has an IAM service account. IAM roles are added to the service account to define a granular
set of permissions for the recorder agent application.   

1)   Create a service account for the recorder agent application:

     ```
     gcloud iam service-accounts create recorder-agent \
      --description="Video recording service" \
      --display-name="RecorderAgent"
     ```

2)   Add the required roles:

     (Note: these commands require certain IAM permissions - ask someone with appropriate privileges to run these for
     you)

     ```
     gcloud projects add-iam-policy-binding <project id> \
     --member=serviceAccount:recorder-agent@<project id>.iam.gserviceaccount.com \
     --role=roles/storage.objectAdmin
     
     gcloud projects add-iam-policy-binding <project ID> \
      --member=serviceAccount:recorder-agent@<project ID>.iam.gserviceaccount.com \ 
      --role=roles/pubsub.subscriber

     gcloud projects add-iam-policy-binding <project ID> \
      --member=serviceAccount:recorder-agent@<project ID>.iam.gserviceaccount.com \
      --role=roles/pubsub.publisher
     ```

### GCS storage bucket

Create the bucket to store recording files with `gsutil`:

`gsutil mb -p <project id> -l US gs://<project id>_recordings`

Bucket properties:
* Location: Multi-region US
* Other properties: default values

### Pub/Sub topic and subscription

Create the Pub/Sub topic:
`gcloud pubsub topics create recording-requests --project <project id>`
 
Create the Pub/Sub subscription for the recorder agents
```
gcloud pubsub subscriptions create recorder-agent-subscription \
--project <project id> \
--topic recording-requests \
--ack-deadline 30
```

### Create the cluster

Create the cluster with `gcloud`:

```
gcloud container clusters create recorder-agent-public \
 --project <GCP project id> \
 --zone <GCP zone> \
 --workload-pool=<project id>.svc.id.goog \
 --enable-stackdriver-kubernetes \ 
 --machine-type <instance type> \
 --disk-size <size in GBs>
```

Notes:
* The node pool options above (`--machine-type` etc.) only pertain to the `default` node pool. 

Some of the notable properties for a cluster created with this command:
* Public cluster
* Zonal cluster, with the k8s control plane hosted in the provided zone
* Workload Identiy enabled cluster wide
* Stackdriver enabled

These are just the current values – several of these will be tweaked/modified in the future (moreover, the current 
clusters will  need to be replaced, as some of these properties cannot be changed after creation).

### Creating Node Pools

If necessary, you can create a new Node pool with `gcloud`. 

For example: 
```
gcloud container node-pools create recorder-agent-pool
 --cluster recorder-agent-public \
 --project uc-beta \
 --zone us-west1-a \
 --num-nodes 1 \
 --machine-type e2-highcpu-8 \
 --disk-size 100
```

This creates a new Node pool in the `recorder-agent-public` cluster of `uc-beta`, with a single `e2-standard-8` compute
instance and a `100 GB` storage volume attached to each instance.

### Configure the cluster

First, configure `kubectl` with credentials for the cluster:
`gcloud container clusters get-credentials <cluster name> --project <GCP project ID>`

#### Get the admin role for the cluster

Add the k8s admin cluster RoleBinding to your account (this is required to install Sealed Secrets and Datadog on the 
cluster):

`kubectl create clusterrolebinding <firstlast>-cluster-admin-binding --clusterrole=cluster-admin 
 --user=<google account name>@dialpad.com`

**Adding the role requires the following GKE IAM role: `container.clusterRoleBindings.create`. If necessary,
either ask someone who has this role to run the above command for you, or have them perform the steps that require the
role.**

#### ServiceAccounts

Run: `kubectl create serviceaccount recorder-agent`

TODO: Create a resource manifest file for this instead.

#### ConfigMaps

Apply the ConfigMaps for the environment to the cluster:

```
cd /path/to/video_recording
kubectl apply -f k8s/configmaps/session_controller/properties.<GCP project id>.yaml
kubectl apply -f k8s/configmaps/properties.recorder.yaml
```

#### Secrets

1)   Follow the steps under [Sealed Secrets setup](#sealed-secrets-setup) to set up Sealed Secrets on the cluster
     
2)   Follow the steps under [Adding a secret to the cluster](#adding-a-secret-to-the-cluster) to generate SealedSecret
     manifests and add the recorder secrets to the cluster

#### Roles

Apply all the Role manifests under `video_recording/k8s/roles` to the cluster:

```
cd /path/to/video_recording
# repeat for all files in the directory
kubectl apply -f k8s/roles/<filename>
```

#### RoleBindings

Apply all the RoleBinding manifests under `video_recording/k8s/rolebindings` to the cluster:

```
cd /path/to/video_recording
# repeat for all files in the directory
kubectl apply -f k8s/rolebindings/<filename>
```

### Set up Workload Identity

#### Background

Workload Identity allows cluster applications to automatically retrieve credentials that can be used to access GCP
services. This is accomplished by configuring a secure mapping between a k8s ServiceAccount and a GCP IAM service
account. When a cluster application uses the k8s ServiceAccount + a GCP client library, the library automatically
queries an endpoint to fetch short-lived credentials associated with the IAM service account. From there, the client
library can perform any actions permitted by the IAM permissions bound to the service account.

For more information, see: https://cloud.google.com/kubernetes-engine/docs/how-to/workload-identity.

#### Setup

Follow the steps at https://cloud.google.com/kubernetes-engine/docs/how-to/workload-identity to set up Workload
Identity:

1.   Workload Identity should be enabled on the cluster when it's first created. See:
     [Create the cluster.](#create-the-cluster)

2.   Create a k8s ServiceAccount for the recorder:
     
     `kubectl create serviceaccount --namespace default recorder-agent`

3.   Create an IAM policy binding between the k8s service account and the GCP service account:
     
     ```
     gcloud iam service-accounts add-iam-policy-binding \
      --role roles/iam.workloadIdentityUser \
      --member "serviceAccount:<GCP project id>.svc.id.goog[default/recorder-agent]" \
      <GCP IAM service account name>@<GCP project id>.iam.gserviceaccount.com
     ```
     
     The IAM service account should be the one created in the [GCP IAM service account steps](#gcp-iam-service-account).

#### Verification

1)   Run the following:
     ```
     kubectl run -it \
      --image google/cloud-sdk:slim \
      --serviceaccount recorder-agent \
      --namespace default \
      workload-identity-test
     ```
     
     This should open an interactive shell within the created Pod.

2)   Run: `gcloud auth list`
    
     You should see the GCP service account listed (and only that account) as the active identity (a `*` in front of the
     account name indicates that identity is active).
     
3)   Clean up the test Pod created in step 1): `kubectl delete deployment workload-identity-test`

Note: Do not set the `GOOGLE_APPLICATION_CREDENTIALS` environment variable for the Pod. When a Google client library
does not see a value for `GOOGLE_APPLICATION_CREDENTIALS` (and Workload Identity is correctly configured), it will
then query for credentials associated with the Pod ServiceAccount, as desired. 

If the setup worked correctly, a recorder agent Pod configured with the `recorder-agent` ServiceAccount
should be able to successfully authenticate and perform operations with the GCP client library. 

### Set up BigQuery storage for the recorder agent logs

Follow the steps below to stream the recorder agent logs to a BiqQuery dataset.

#### Create a BigQuery dataset:

1.   In the GCP console, select BigQuery
2.   On the left panel menu, click on the GCP project name 
3.   Click on "CREATE DATASET" 
4.   For the dataset ID, use `<GCP Project Id>RecorderAgentLogs` e.g. `UCStagingRecorderAgentLogs`
5.   Set the data location to "US"
6.   Use the defaults for the other options

#### Commands to create log sink in \<GCP project id\>:
```
gcloud beta logging sinks create recorder-agent-sink-bigquery \
  bigquery.googleapis.com/projects/ucstaging/datasets/<GCP project id>RecorderAgentLogs --project=<GCP project id> \
  --log-filter="resource.type=k8s_container AND resource.labels.project_id=<GCP project id> AND resource.labels.cluster_name=<Recorder cluster name> AND resource.labels.namespace_name=default AND labels.k8s-pod/app=recorder-agent AND labels.k8s-pod/environment=<GCP project id>"
```

Follow the steps below to 1) associate the service account referred to in the above command with the newly created BQ
dataset and 2) add the "BigQuery Data Editor" role to the service account

**Note the following steps require certain IAM permissions – find someone who has them.**

1.   In the GCP console, select "BigQuery"
2.   From the left panel menu, select the dropdown for the GCP project, and then select the dataset created above in the
     expanded options
3.   Click the "SHARE DATASET" button 
4.   Click "Add members" – add the service account from the above sink creation command
5.   Select the "BigQuery Data Editor" role and complete the process.


# Docker Images and Registry

Each Pod runs multiple containers; the images are specified in the Pod spec for the workload.

Images are hosted in Google Container Registry. There is a private registry in each GCP project – images are hosted in 
the same project where the cluster is hosted. There are several ways to build and deploy the recorder images. Below, we highlight 2 methods.

**REMINDER: IMAGES WON'T BE PICKED UP UNTIL MANIFEST IS UPDATED**

### GitHub Actions
By far the easiest to get started is using GitHub Actions. We have created a dispatch workflow which is a manual event
where a user will manually trigger the workflow runs.

- To get started, navigate to the `firespotter` repository and click on the `Actions` tab.
- Under the `Workflows` section on the left, click on `recorder-push`.
- Click on `Run workflow` dropdown that appears on the right.
- Choose the proper branch and environment (ucstaging, uc-beta, uberconf) you want to build and ` Run workflow`
- Leave the tag as default, unless you are planning to use your own tag (`bpham` or others)

### Build & Deploy locally

Configure Docker with [GCR credentials](https://cloud.google.com/container-registry/docs/advanced-authentication#gcloud-helper) using this command `gcloud auth configure-docker`

The GCR images for the project should follow the format `gcr.io/<GCP project id>:<image name>:<tag>`

Ex) Use the Docker CLI locally to build/tag images, and push them to GCR

1)   Build the project images:
     ```
     cd path/to/firespotter/uc_headless
     
     docker build -t recording-session-controller -f video_recording/session_controller/Dockerfile .
     docker build -t recording-browser-agent video_recording/browser_agent/Dockerfile .
     
     # or name the images in the GCR format (see step below for more detail)
     docker built -t gcr.io/ucstaging/recording-session-controller:latest -f video_recording/session_controller/Dockerfile .
     docker built -t gcr.io/ucstaging/recording-browser-agent:latest -f video_recording/browser_agent/Dockerfile .
     ```

2)   Tag the images named `recording-session-controller` and `recording-browser-agent` in the
     GCR format, with the `ucstaging` registry and `latest` tag: 

     ```
     docker tag recording-session-controller gcr.io/ucstaging/recording-session-controller:latest
     docker tag recording-browser-agent gcr.io/ucstaging/recording-browser-agent:latest
     ```
     
3)   Push the images to GCR:

     ```
     docker push gcr.io/ucstaging/recording-session-controller:latest
     docker push gcr.io/ucstaging/recording-browser-agent:latest
     ```
     

If you need to build the browser agent base image, you can build the image with a command such as:

> docker build -t recording-browser-agent common/browser_agent_base

### Datadog

We use Flux to deploy a Datadog helm chart to the cluster. For details about configuration, take a look at 
[base/datadog config](https://github.com/dialpad/ucr-infra/tree/main/apps/base/datadog).


## Secrets

Application secrets are generated and stored with the standard process described in the 
[telephony secrets doc](https://sites.google.com/a/dialpad.com/internal/telephony/cloud/secrets-store) and the
[UV secrets doc](https://sites.google.com/a/dialpad.com/internal/product-engineering/ubervoice/secrets).

The GCS secrets bucket is the source of the truth.

Cluster secrets are secured via Sealed Secrets and (in the future) at-rest encryption implemented with a third-party
KMS, as described in https://kubernetes.io/docs/tasks/administer-cluster/encrypt-data.

Video recording secrets:

1) The recorder secret, shared by both UC and the agent. The payloads for API requests made by the recorder agent are 
signed with this secret. UC uses the same secret to verify the signatures.

2) The UC secret, which is used to sign the recorder token that allows the headless client to join a conference.

### Sealed Secrets

#### Overview
Cluster secrets are asymmetrically encrypted with Sealed Secrets: https://github.com/bitnami-labs/sealed-secrets.

Sealed Secrets provides a mechanism to securely encrypt cluster secrets and create a declarative spec for the "sealed"
secret that can be safely committed to version control. The resulting sealed secret can be added to the cluster with a 
simple `kubectl apply` command / request to the API server, as you might with other k8s resources. 

Sealed Secrets consists of two parts:

1.   A Sealed Secrets Controller (installed on the cluster itself) that generates asymmetric key pairs used to encrypt 
     secrets. The generated keys are persisted in `etcd`.

2.   `kubeseal` is a client-side utility that fetches the public key cert from the Controller at runtime and 
     encrypts the provided secret.
     
     `kubeseal` generates a `SealedSecret` Custom Resource Definition containing the encrypted secret data.
     
     The `SealedSecret` object definition can safely be shared in otherwise insecure channels; the file should be 
     committed to VCS in the project `k8s/secrets` folder.
     
Note: a comprehensive secret solution should also incorporate at-rest encryption for the private keys stored in `etcd`. 
This is a future TODO.

#### Sealed Secrets setup

The sealed secrets controller is deployed with Flux. The configuration is defined in the ucr-infra.
See the base configuration
[here for more details](https://github.com/dialpad/ucr-infra/tree/main/apps/base/sealed-secrets).

#### Adding a secret to the cluster

The following demonstrates how to manually seal the recorder secret and apply it to the cluster.

The steps are adapted from the 
[instructions here](https://github.com/bitnami-labs/sealed-secrets/tree/f903596e6561bd3151e9b2d12591472e886f24da#usage).

[In the future, this will be handled in a CI/automation job (at least the part that applies the secret to the cluster)]

1.   Set an environment variable for the GCP project id where the cluster is hosted:
     
     Bash: `export GCP_PROJECT_ID=<project id>`
     
2.   Configure `kubectl` credentials for the desired cluster: 

     `gcloud container clusters get-credentials --project $GCP_PROJECT_ID`

3.   Fetch the recorder agent secret value from the secrets bucket and copy the contents: 

     `path/to/firespotter/uberconf/manage_secrets.py get $GCP_PROJECT_ID -n VIDEO_RECORDER_AGENT_SIGNING_SECRET 
     --plaintext`

4.   Create the k8s Secret definition and store it in a file: 

     `echo -n "<plaintext secret content>" | 
     kubectl create secret generic -n recorder recorder-agent-secret --dry-run=client --from-file=secret=/dev/stdin 
     -o json >recorder_agent_secret.<project id>.json`

     `recorder_agent_secret.<project id>.json` includes the base64-encoded secret value; this file should not be shared 
      anywhere – delete it once these steps are completed.

5.   Use `kubeseal` to "seal" the secret:

     `kubeseal --controller-namespace flux-system <recorder_agent_secret.<project id>.json >sealed_recorder_agent_secret.<project id>.json` 
   
     This generates the `SealedSecret` object definition in `sealed_recorder_agent_secret.<project id>.json`.

6.   Apply the secret to the cluster: `kubectl create -f sealed_recorder_agent_secret.json`
     
     If successful, the secret should now be usable like a standard k8s Secret. 
     
5.   Verify the Secret and SealedSecret:

     `kubectl describe secret -n recorder recorder-agent-secret`  
     
     `kubectl describe sealedsecret -n recorder recorder-agent-secret`

7.   Commit the secret to version control under the `uc_headless/video_recording/apps/overlays/<env>` folder for the
     appropriate environment.

8.   Remove the local secret file `recorder_agent_secret.<project id>.json`

#### Sealed-Secrets Backup
Backup of the encryption sealedsecrets private keys were retrieved using the command below and stored in 1Password in the `infrastructure vault`

```
kubectl get secret -n flux-system -l sealedsecrets.bitnami.com/sealed-secrets-key -o yaml >sealedsecret-master-<env>.key
```

#### Sealed-Secrets Restore
To restart from backup from a disaster, you will need to put that secret key back before starting the controller. Or if the new controller is already up, you can just replace it the newly-created secrets and restart the controller.

```
$ kubectl apply -f sealedsecret-master-<env>.key
$ kubectl delete pod -n flux-system -l name=sealed-secrets
```
