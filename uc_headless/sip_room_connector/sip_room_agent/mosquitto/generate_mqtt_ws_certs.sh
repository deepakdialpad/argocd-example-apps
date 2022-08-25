#/bin/bash
set -e

# Generate self signed certs with self CA

# Conditonal statement to allow for dev CA certificates
if [ ! -f localhost-ca.key ]
then
openssl genrsa -out localhost-ca.key 2048
openssl req -new -x509 -nodes -days 365 -key localhost-ca.key -out localhost-ca.pem -subj "/C=US/ST=California/O=Dialpad/CN=localhost"
fi

openssl genrsa -out localhost.key 2048
openssl req -nodes -new -key localhost.key -out localhost.csr -subj "/C=US/ST=California/O=Dialpad/CN=localhost"

cat > localhost.ext << EOF
authorityKeyIdentifier=keyid,issuer
basicConstraints=CA:FALSE
keyUsage = digitalSignature, nonRepudiation, keyEncipherment, dataEncipherment
subjectAltName = @alt_names
[alt_names]
DNS.1 = localhost
EOF


openssl x509 -req -sha256 -days 365 -extfile localhost.ext -CAcreateserial -CA localhost-ca.pem -CAkey localhost-ca.key  -in localhost.csr -out localhost.crt

# Copy all self-signed certs into their appropriate place and update CA cert databased in the image.

rm localhost-ca.key
mv localhost-ca.pem /usr/share/localhost-ca.pem
mv localhost.key /usr/share/localhost.key
mv localhost.crt /usr/share/localhost.crt
chmod 644 /usr/share/localhost.key
cp /usr/share/localhost-ca.pem /usr/share/ca-certificates/mozilla/localhost-ca.crt
chmod 644 /usr/share/ca-certificates/mozilla/localhost-ca.crt && update-ca-certificates

mkdir -p /root/.pki/nssdb
certutil -d sql:/root/.pki/nssdb --empty-password -N
certutil -d sql:/root/.pki/nssdb -A -t "C,," -n "Interop WS Cert" -i /usr/share/ca-certificates/mozilla/localhost-ca.crt