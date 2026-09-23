#pragma once

#include "Util.h"
#include "Config.h"
#include "NCSProtocol.h"
#include "NetInterface.h"

enum class NCSErr : int
{
	OK = 0,
	NOT_CONNECTED = 100,
	ERR_BUILD,
	ERR_PARSE,
	ERR_BODY,
	ERR_NETWORK,						// check neterr field
	ERR_BAD_RC,
	NOT_IMPL,
};

#define IGNORE_RC	false
#define CHECK_RC	true

#define NCS_IFACE_FIXED_LEN		12
#define MAX_IFACE_TAIL			256

struct NCSInterfaceInfo
{
	int32_t		enabled{ 0 };
	int32_t		baud{ 0 };
	uint32_t	served{ 0 };			// requests served, diff this to see MMI traffic on the bus
	byte		tail[MAX_IFACE_TAIL]{};	// the two device name fields, layout unknown until we see bytes
	ushort		tail_len{ 0 };

	void Print(int com) const;
};


class NCSClient
{
	char			host[MAX_HOST_LEN];
	ushort			port;
	const Config*	cfg{ nullptr };

	sockaddr_in_t	remote{};
	bool			resolved{ false };

	NetErr			neterr{ NetErr::OK };

	/*
	 */
	NCSErr Transact(const NCSRequest* req, NCSResponse* resp, bool check_rc = CHECK_RC);


	// TODO: 1b + need better args for this definitely
	NCSErr SendPacket(int com, byte* frame, ushort frame_len, byte* reply, size_t cap, size_t* reply_len);
	//friend class PumpClient;

public:

	NCSClient(const char* host, ushort port, const Config* cfg)
		: port(port), cfg(cfg)
	{
		strncpy_s(this->host, MAX_HOST_LEN, host, _TRUNCATE);
	}

	NCSErr Init();
	NCSErr GetVersion(char* buf, size_t cap);
	NCSErr GetName(char* buf, size_t cap);
	NCSErr GetInterfaces(uint& n);

	// NOTE: watch for rc UNKNOWN_SERIAL_PORT re. logical_com1
	NCSErr QueryInterface(int com, NCSInterfaceInfo* info, NCSResponse* resp);

	inline ushort	LSerial(int com) { return (ushort)(com - 1 + cfg->logical_com1); }
	inline NetErr	NetErr() const { return neterr;  }
};
