FROM alpine:3.15.0
RUN apk --no-cache add bash curl
COPY sip_room_connector/init/fetch_public_node_ip.sh fetch_public_node_ip.sh
RUN chmod +x fetch_public_node_ip.sh
CMD ["./fetch_public_node_ip.sh"]
