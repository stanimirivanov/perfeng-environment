printf '[default]\ns3 =\n    addressing_style = path\n' > /tmp/awsconfig
aws_s3() {
  aws --endpoint-url http://seaweedfs.perf-platform.svc.cluster.local:8333 \
    --cli-connect-timeout 10 --cli-read-timeout 30 s3api "$@"
}
if ! aws_s3 head-bucket --bucket perfeng-artifacts 2>/tmp/head-error; then
  if ! grep -Eq '\(404\)|Not Found' /tmp/head-error; then
    echo 'Bucket lookup failed for a reason other than absence' >&2
    exit 1
  fi
  aws_s3 create-bucket --bucket perfeng-artifacts >/dev/null
fi
aws_s3 head-bucket --bucket perfeng-artifacts >/dev/null
echo 'Artifact bucket exists and is accessible'
