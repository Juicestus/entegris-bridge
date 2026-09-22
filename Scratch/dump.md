# Current Codebase State

Header files only

## Config.h

```cpp
#pragma once

#include "Util.h"

#define MAX_HOST_LEN	256

struct Config
{
	char	test_host[MAX_HOST_LEN];
	ushort	test_port;
	int		test_com;
	int		test_pump_addr;

	/* 1 = little endian, 0 = big endian */
	bool	little_endian;
	/* 1 = size includes header, 0 = size excludes header */
	bool	size_includes_header;

	/* 1 = COM1 is logical serial 1, 0 = logical serial 0*/
	ushort	logical_com1;

	ushort	timeout_ms;
	char	log_path[MAX_PATH];

	/* 
	 * Default config 
	 */
	Config();
	/*
	 * Load the config from a file path
     */
	int Load(const char* path);

	static Config FromArgs(int argc, char** argv);

	void Print() const;
};

``` 

## NCSProtocol.h

```cpp
#pragma once

#include "Util.h"
#include "Config.h"

#define MAX_NCS_PKT		4096

enum class NCSCommand : uint16_t
{
	NCS_SEND_PACKET = 0,		// tunnels a pump frame, 1b
	NCS_GET_INTERFACES = 1,
	NCS_GET_NAME = 2,
	NCS_GET_VERSION = 3,
	NCS_QUERY_INTERFACE = 4,
	NCS_LOCK_INTERFACE = 5,		// never sent
	NCS_UNLOCK_INTERFACE = 6,	// never sent
};

enum class NCSReturnCode : uint16_t
{
	ERR_NO_ERROR = 0,
	ERR_INTERNAL = 1001,
	ERR_UNKNOWN_COMMAND = 1002,
	ERR_MALFORMED_PACKET = 1003,
	ERR_UNKNOWN_SERIAL_PORT = 1004,			// also what a wrong logical_com1 gives us
	ERR_DISABLED_SERIAL_PORT = 1005,
	ERR_INPUT_INVALID_CRC = 1006,			// our frame's CRC, checked before it hits the bus
	ERR_EXTRA_DATA = 1007,
	ERR_INVALID_SERIAL_DATA = 1008,
	ERR_LOCKED_INTERFACE = 1010,			// no 1009 in the spec
	ERR_NOT_LOCKED_INTERFACE = 1011,
	ERR_INVALID_LOCK_CODE = 1012,
	ERR_SERIAL_COMMAND_FAILED = 1013,
	ERR_SERIAL_SHORT_READ = 1014,
	ERR_SERIAL_READ_TIMEOUT = 1015,
	ERR_SERIAL_OUTPUT_INVALID_CRC = 1016,	// pump's reply CRC
};


#pragma pack(push, 1)
struct NCSReqHeader
{
	NCSCommand	cmd;
	uint16_t	size;			// see cfg->size_includes_header
	uint16_t	timeout;		// TODO: units? 
	uint16_t	ctrl;			// TODO: look into CTRL_* bits
	uint16_t	lserial;		 
	uint32_t	lock;		
	uint32_t	reserved;	
};

struct NCSRespHeader
{
	NCSReturnCode	rc;
	uint16_t		size;
	uint16_t		reserved[3];
	uint32_t		reserved2[2];
};
#pragma pack(pop)

/* Enforce header length */
#define NCS_HDR_LEN		18

static_assert(sizeof(NCSReqHeader) == NCS_HDR_LEN, "");
static_assert(sizeof(NCSRespHeader) == NCS_HDR_LEN, "");


struct NCSRequest
{
	NCSCommand	cmd{ NCSCommand::NCS_SEND_PACKET };
	ushort		lserial{ 0 };
	ushort		ctrl{ 0 };
	byte*		data{ nullptr };
	ushort		data_len{ 0 };

	int Build(byte* buf, size_t cap, const Config* cfg);
};

struct NCSResponse
{
	byte			buf[MAX_NCS_PKT];
	size_t			len;
	NCSReturnCode	rc;
	byte*			body;
	size_t			body_len;

	int Parse(const Config* cfg);
};

``` 

## NetInterface.h

```cpp
#pragma once

#include "Util.h"

enum NetInterfaceErr
{
	NET_OK = 0,
	NET_ERR_RESOLVE,	// bad IP / no DNS entry
	NET_ERR_SOCKET,
	NET_ERR_CONNECT,
	NET_ERR_SEND,
	NET_ERR_RECV,
	NET_ERR_TIMEOUT,
	NET_NOT_OPEN,
	NET_ALREADY_OPEN,
};

class NetInterface
{
	SOCKET sock{ INVALID_SOCKET };
	bool open{ false };

public:
	static int InitNet();
	static void CloseNet();

	static int ResolveHost(const char* host, ushort port, struct sockaddr_in* remote);

	NetInterface() {}

	/* 
	 * NOTE: SO_RCVTIMEO is a DWORD of ms on windows but a timeval on POSIX
	 * and blocking connect() ignores it 
	 */
	int Open(struct sockaddr_in* remote, ushort timeout_ms);
	
	int Send(byte* buf, size_t len);

	/* until peer closes or timeout, TODO: exact read once size field is confirmed */
	int RecvAll(byte* buf, size_t cap, size_t* len);

	int Close();

	~NetInterface();
};

``` 

## Util.h

```cpp
#pragma once

#ifdef _WIN32
#define _CRT_SECURE_NO_WARNINGS	// TODO: compat layer
#endif

#include <iostream>
#include <fstream>
#include <string>

#include <cstdio>
#include <cstdint>
#include <cassert>
#include <cstring>

#ifdef _WIN32

#define _WINSOCK_DEPRECATED_NO_WARNINGS
#include <winsock2.h>
#include <ws2tcpip.h>
#include <Windows.h>
#pragma comment(lib, "ws2_32.lib")

#else
/* unix specific */
#endif

using uint = unsigned int;
using ushort = unsigned short;

#define FATAL(FMT, ...)	fprintf(stderr, FMT "\n", __VA_ARGS__)
#define ARR_LEN(A)		(sizeof(A) / sizeof(*A))

#define BOOLTF(BOOL)	( (BOOL) ? "TRUE" : "FALSE" )		// bool -> true/false string
#define BOOLYN(BOOL)	( (BOOL) ? "YES" : "NO" )			// bool -> yes/no string 

// serial layer is LE per CommSpec, NCS header assumed LE too, so these are
// no-ops on an LE host unless the config says the NCS header is BE
// TODO: make portable
__forceinline uint16_t Wire16(uint16_t v, bool be) 
{ 
	return be ? _byteswap_ushort(v) : v; 
}
__forceinline uint32_t Wire32(uint32_t v, bool be) 
{ 
	return be ? (uint32_t)_byteswap_ulong(v) : v; 
}


``` 

