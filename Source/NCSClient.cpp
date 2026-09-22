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
NCSErr NCSClient::Transact(const NCSRequest* req, NCSResponse* resp, NetErr& neterr)
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

	return NCSErr::OK;
}


/*
 * 1b: tunnel a complete pump frame to the unit on com. Not implemented yet.
 */
NCSErr NCSClient::SendPacket(int com, byte* frame, ushort frame_len, byte* reply, size_t cap, size_t* reply_len)
{
	return NCSErr::NOT_IMPL;	// TODO: phase 1b
}






/*
 * 
 */
NCSErr NCSClient::GetCmd(NCSCommand cmd, NCSResponse* resp, NetErr& neterr)
{
	NCSRequest req(cmd);
	NCSErr status;

	if ((status = Transact(&req, resp, neterr)) != NCSErr::OK)
		return status;
	if (resp->rc != NCSReturnCode::NO_ERR)
	{
		FATAL("Command %d responded with bad code %hu", (int)cmd, (uint16_t)resp->rc);
		return NCSErr::ERR_BAD_RC;
	}
	return NCSErr::OK;
}


NCSErr NCSClient::GetVersion(char* buf, size_t cap, NetErr& neterr)
{
	NCSResponse resp;
	NCSErr status;
	if ((status = GetCmd(NCSCommand::GET_VERSION, &resp, neterr)) == NCSErr::OK)
		resp.CopyStrOut(buf, cap);		// copy out if is ok
	return status
}

NCSErr NCSClient::GetName(char* buf, size_t cap, NetErr& neterr)
{
	NCSResponse resp;
	NCSErr status;
	if ((status = GetCmd(NCSCommand::GET_NAME, &resp, neterr)) == NCSErr::OK)
		resp.CopyStrOut(buf, cap);		
	return status
}

