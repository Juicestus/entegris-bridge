#include "NCSClient.h"


NetErr NCSClient::Init()
{
	NetErr status;
	if ((status = NetInterface::ResolveHost(host, port, &remote)) != NetErr::OK)
		return status;
	connected = true;
	return NetErr::OK;
}



/*
 * 
 */
NCSErr NCSClient::Transact(const NCSRequest* req, NCSResponse* resp, NetErr& neterr, bool check_rc /*= CHECK_RC*/)
{
	if (!connected) return NCSErr::NOT_CONNECTED;

	byte pkt[NCS_MAX_PKT];
	int pkt_len;
	if ((pkt_len = ((NCSRequest*)req)->Build(pkt, sizeof(pkt), cfg)) < 0)		// TODO: make Build const
		return NCSErr::ERR_BUILD;

	//PrintBytes("-->", pkt, pkt_len);
	NetInterface net;

	// socket timeout has to outlast the header's timeout field, or we give up
	// before the NCS can tell us it timed out on the serial side (1b)
	if ((neterr = net.Open(&remote, cfg->timeout_ms + 1000)) != NetErr::OK)
	{
		FATAL("NCS transaction failed due to network failure %d on Open", (int)neterr);
		return NCSErr::ERR_NETWORK;
	}

	if ((neterr = net.Send(pkt, pkt_len)) != NetErr::OK)
	{
		FATAL("NCS transaction failed due to network failure %d on Send", (int)neterr);
		net.Close();
		return NCSErr::ERR_NETWORK;
	}

	if ((neterr = net.RecvAll(resp->buf, sizeof(resp->buf), &resp->len)) != NetErr::OK)
	{
		FATAL("NCS transaction failed due to network failure %d on Recv", (int)neterr);
		net.Close();
		return NCSErr::ERR_NETWORK;
	}
	net.Close();

	//PrintBytes("<--", resp->buf, resp->len);

	if (resp->Parse(cfg))
		return NCSErr::ERR_PARSE;

	if (check_rc && resp->rc != NCSReturnCode::NO_ERR)
	{
		FATAL("Command %d responded with bad code %hu", (int)req->cmd, (uint16_t)resp->rc);
		return NCSErr::ERR_BAD_RC;
	}
	return NCSErr::OK;
}


/*
 * 1b: tunnel a complete pump frame to the unit on com. Not implemented yet.
 */
NCSErr NCSClient::SendPacket(int com, byte* frame, ushort frame_len, byte* reply, size_t cap, size_t* reply_len)
{
	return NCSErr::NOT_IMPL;	// TODO: phase 1b
}


NCSErr NCSClient::GetVersion(char* buf, size_t cap, NetErr& neterr)
{
	NCSRequest req(NCSCommand::GET_VERSION);
	NCSResponse resp;
	NCSErr status;
	if ((status = Transact(&req, &resp, neterr)) == NCSErr::OK)
		resp.CopyStrOut(buf, cap);		// copy out if is ok
	return status;
}

NCSErr NCSClient::GetName(char* buf, size_t cap, NetErr& neterr)
{
	NCSRequest req(NCSCommand::GET_NAME);
	NCSResponse resp;
	NCSErr status;
	if ((status = Transact(&req, &resp, neterr)) == NCSErr::OK)
		resp.CopyStrOut(buf, cap);		// copy out if is ok
	return status;
}

NCSErr NCSClient::GetInterfaces(uint& n, NetErr& neterr)
{
	NCSRequest req(NCSCommand::GET_INTERFACES);
	NCSResponse resp;
	NCSErr status;
	if ((status = Transact(&req, &resp, neterr)) != NCSErr::OK)
		return status;

	/* decode as 32 and 16 bit */
	uint32_t v32 = 0;
	uint16_t v16 = 0;
	if (resp.body_len >= 4)
	{
		v32 = cfg->Host32(*(uint32_t*)resp.body);
		printf("  GET_INTERFACES as UINT32:  %u\n", v32);
	}
	if (resp.body_len >= 2)
	{
		v16 = cfg->Host16(*(uint16_t*)resp.body);
		printf("  GET_INTERFACES as UINT16: %hu\n", v16);
	}

	/* fill n with correct value */
	if (v32 >= 1 && v32 <= 64)
	{
		n = v32;
		return NCSErr::OK;
	}
	if (v16 >= 1 && v16 <= 64)
	{
		n = v16;
		return NCSErr::OK;
	}

	// TODO: reduce once tested
	FATAL("Couldn't decode an interface count from %zu bytes", resp.body_len);
	return NCSErr::ERR_BODY;
}


NCSErr NCSClient::QueryInterface(int com, NCSInterfaceInfo* info, NCSResponse* resp, NetErr& neterr)
{
	*info = NCSInterfaceInfo();

	NCSRequest req(NCSCommand::QUERY_INTERFACE);
	req.lserial = LSerial(com);

	NCSErr status;
	if ((status = Transact(&req, resp, neterr)) != NCSErr::OK)
		return status;

	// check before the subtraction below, body_len is unsigned and would underflow
	if (resp->body_len < NCS_IFACE_FIXED_LEN)
	{
		FATAL("Interface info is %zu bytes, need at least %d", resp->body_len, NCS_IFACE_FIXED_LEN);
		return NCSErr::ERR_BODY;
	}

	info->enabled = (int32_t)cfg->Host32(*(uint32_t*)(resp->body + 0));
	info->baud = (int32_t)cfg->Host32(*(uint32_t*)(resp->body + 4));
	info->served = cfg->Host32(*(uint32_t*)(resp->body + 8));

	info->tail_len = (ushort)std::min(resp->body_len - NCS_IFACE_FIXED_LEN, (size_t)MAX_IFACE_TAIL);
	if (info->tail_len)
		memcpy(info->tail, resp->body + NCS_IFACE_FIXED_LEN, info->tail_len);

	return NCSErr::OK;
}


void NCSInterfaceInfo::Print(int com) const
{
	printf("  COM%d  %-3s  baud %6d  served %8u  tail %3hu",
		com,
		BOOLYN(enabled),
		baud,
		served,
		tail_len);

	// printable chars out of the tail until we know the layout
	if (tail_len)
	{
		printf("  \"");
		for (ushort i = 0; i < tail_len; i++)
			if (tail[i] >= 32 && tail[i] < 127) putchar(tail[i]);
		putchar('"');
	}
	putchar('\n');
}





